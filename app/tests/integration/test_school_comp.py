"""Integration tests for the staff complimentary-access grant."""

import asyncio
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.event import Event
from app.models.product import Product
from app.models.school import School, SchoolBookbotType, SchoolState
from app.models.subscription import Subscription, SubscriptionType
from app.services.school_access import (
    SCHOOL_COMP_GRANTED_EVENT_TITLE,
    STAFF_COMP_GRANT_SOURCE,
    ensure_comp_product_async,
    grant_staff_comp,
    staff_comp_id,
)


def _grant_payload(days: int, *, key: str | None = None) -> dict:
    return {
        "days": days,
        "idempotency_key": key or f"test-{uuid4()}",
        "reason": "Local integration test",
        "campaign_id": "test-campaign",
    }


def _make_inactive_school(session) -> School:
    school = School(
        name=f"Comp Test School {datetime.utcnow().timestamp()}",
        country_code="AUS",
        state=SchoolState.INACTIVE,
        bookbot_type=SchoolBookbotType.HUEY_BOOKS,
    )
    session.add(school)
    session.commit()
    session.refresh(school)
    return school


def test_staff_can_comp_a_school(client, session, test_wrivetedadmin_account_headers):
    """Staff grants complimentary access → school ACTIVE with a comp subscription."""
    school = _make_inactive_school(session)
    wid = school.wriveted_identifier

    resp = client.post(
        f"/v1/admin/schools/{wid}/comp",
        headers=test_wrivetedadmin_account_headers,
        json=_grant_payload(90),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["outcome"] == "granted"
    assert body["idempotent_replay"] is False
    assert body["access_until"] is not None

    grant = (
        session.execute(
            select(Subscription).where(Subscription.id == staff_comp_id(wid))
        )
        .scalars()
        .first()
    )
    assert grant is not None and grant.is_active
    assert grant.stripe_customer_id == ""
    assert grant.info["source"] == STAFF_COMP_GRANT_SOURCE

    # Read fresh from the DB — the endpoint flipped state via its own (async)
    # session, so expire this session's identity-map copy first.
    session.expire_all()
    school = (
        session.execute(select(School).where(School.wriveted_identifier == wid))
        .scalars()
        .first()
    )
    assert school.state == SchoolState.ACTIVE


def test_comp_remains_compatible_with_pre_idempotency_clients(
    client, session, test_wrivetedadmin_account_headers
):
    school = _make_inactive_school(session)

    response = client.post(
        f"/v1/admin/schools/{school.wriveted_identifier}/comp",
        headers=test_wrivetedadmin_account_headers,
        json={"days": 90},
    )

    assert response.status_code == 200, response.text
    assert response.json()["outcome"] == "granted"


def test_comp_re_grant_extends_never_shortens(
    client, session, test_wrivetedadmin_account_headers
):
    school = _make_inactive_school(session)
    wid = school.wriveted_identifier

    long_grant = client.post(
        f"/v1/admin/schools/{wid}/comp",
        headers=test_wrivetedadmin_account_headers,
        json=_grant_payload(300),
    )
    assert long_grant.status_code == 200, long_grant.text
    long_until = long_grant.json()["access_until"]

    # A shorter re-grant must not shorten the existing access.
    short_grant = client.post(
        f"/v1/admin/schools/{wid}/comp",
        headers=test_wrivetedadmin_account_headers,
        json=_grant_payload(30),
    )
    assert short_grant.status_code == 200, short_grant.text
    assert short_grant.json()["outcome"] == "unchanged"
    assert short_grant.json()["access_until"] == long_until


def test_comp_request_is_idempotent_and_audited(
    client, session, test_wrivetedadmin_account_headers
):
    school = _make_inactive_school(session)
    wid = school.wriveted_identifier
    payload = _grant_payload(90, key=f"campaign-2026-{wid}")

    first = client.post(
        f"/v1/admin/schools/{wid}/comp",
        headers=test_wrivetedadmin_account_headers,
        json=payload,
    )
    replay = client.post(
        f"/v1/admin/schools/{wid}/comp",
        headers=test_wrivetedadmin_account_headers,
        json=payload,
    )

    assert first.status_code == 200, first.text
    assert replay.status_code == 200, replay.text
    assert replay.json() == {**first.json(), "idempotent_replay": True}

    events = (
        session.execute(
            select(Event).where(
                Event.school_id == school.id,
                Event.title == SCHOOL_COMP_GRANTED_EVENT_TITLE,
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1
    assert events[0].info == {
        "access_until": first.json()["access_until"],
        "campaign_id": "test-campaign",
        "days": 90,
        "description": events[0].description,
        "granted_by": str(events[0].user_id),
        "idempotency_key": payload["idempotency_key"],
        "outcome": "granted",
        "previous_expiration": None,
        "reason": "Local integration test",
        "source": STAFF_COMP_GRANT_SOURCE,
        "state": SchoolState.ACTIVE.value,
    }


def test_comp_refuses_school_with_active_paid_subscription(
    client, session, test_wrivetedadmin_account_headers
):
    school = _make_inactive_school(session)
    wid = school.wriveted_identifier
    session.add(Product(id=f"price_paid_{school.id}", name="Paid"))
    session.flush()
    session.add(
        Subscription(
            id=f"sub_paid_{school.id}",
            product_id=f"price_paid_{school.id}",
            stripe_customer_id="cus_real",
            school_id=wid,
            type=SubscriptionType.SCHOOL,
            is_active=True,
            expiration=datetime(2099, 1, 1),
        )
    )
    session.commit()

    resp = client.post(
        f"/v1/admin/schools/{wid}/comp",
        headers=test_wrivetedadmin_account_headers,
        json=_grant_payload(90),
    )
    assert resp.status_code == 409, resp.text


def test_non_staff_cannot_comp(client, session, admin_of_test_school_headers):
    school = _make_inactive_school(session)
    resp = client.post(
        f"/v1/admin/schools/{school.wriveted_identifier}/comp",
        headers=admin_of_test_school_headers,
        json=_grant_payload(90),
    )
    assert resp.status_code in (401, 403), resp.text


@pytest.mark.asyncio
async def test_concurrent_comp_request_is_applied_once(settings, session):
    school = _make_inactive_school(session)
    school_id = school.wriveted_identifier
    idempotency_key = f"concurrent-{school_id}"
    engine = create_async_engine(
        settings.SQLALCHEMY_ASYNC_URI,
        pool_size=2,
        max_overflow=0,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def apply_grant():
        async with session_factory() as concurrent_session:
            return await grant_staff_comp(
                concurrent_session,
                school_id,
                days=90,
                account=None,
                idempotency_key=idempotency_key,
                reason="Concurrent integration test",
                campaign_id=None,
            )

    try:
        results = await asyncio.gather(apply_grant(), apply_grant())
    finally:
        await engine.dispose()

    assert sorted(result.idempotent_replay for result in results) == [False, True]
    events = (
        session.execute(
            select(Event).where(
                Event.school_id == school.id,
                Event.title == SCHOOL_COMP_GRANTED_EVENT_TITLE,
                Event.info["idempotency_key"].astext == idempotency_key,
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1


@pytest.mark.asyncio
async def test_concurrent_comp_product_creation_is_atomic(settings, session):
    product_id = f"comp_product_race_{uuid4()}"
    engine = create_async_engine(
        settings.SQLALCHEMY_ASYNC_URI,
        pool_size=2,
        max_overflow=0,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def create_product():
        async with session_factory() as concurrent_session:
            await ensure_comp_product_async(
                concurrent_session,
                product_id,
                "Concurrent comp product",
            )
            await concurrent_session.commit()

    try:
        await asyncio.gather(create_product(), create_product())
    finally:
        await engine.dispose()

    products = (
        session.execute(select(Product).where(Product.id == product_id)).scalars().all()
    )
    assert len(products) == 1


@pytest.mark.asyncio
async def test_lapse_sweep_expires_staff_comp(async_session):
    """A staff comp is expired by the lapse sweep once past its expiration."""
    from app.api.internal import handle_lapse_expired_schools
    school = School(
        name=f"Comp Sweep School {uuid4().hex[:8]}",
        wriveted_identifier=uuid4(),
        state=SchoolState.INACTIVE,
    )
    async_session.add(school)
    await async_session.flush()

    await grant_staff_comp(
        async_session,
        school.wriveted_identifier,
        days=90,
        account=None,
        idempotency_key=f"sweep-{school.wriveted_identifier}",
        reason="Lapse sweep integration test",
        campaign_id=None,
    )
    assert school.state == SchoolState.ACTIVE

    # Backdate the comp so the sweep sees it as expired.
    grant = (
        await async_session.execute(
            select(Subscription).where(
                Subscription.id == staff_comp_id(school.wriveted_identifier)
            )
        )
    ).scalar_one()
    grant.expiration = datetime.utcnow() - timedelta(days=1)
    async_session.add(grant)
    await async_session.commit()

    await handle_lapse_expired_schools(async_session)

    await async_session.refresh(school)
    assert school.state == SchoolState.INACTIVE

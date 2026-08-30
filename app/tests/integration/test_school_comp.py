"""Integration tests for the staff complimentary-access grant."""

from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.product import Product
from app.models.school import School, SchoolBookbotType, SchoolState
from app.models.subscription import Subscription, SubscriptionType
from app.services.school_access import STAFF_COMP_GRANT_SOURCE, staff_comp_id


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
        json={"days": 90},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["outcome"] == "granted"
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


def test_comp_re_grant_extends_never_shortens(
    client, session, test_wrivetedadmin_account_headers
):
    school = _make_inactive_school(session)
    wid = school.wriveted_identifier

    long_grant = client.post(
        f"/v1/admin/schools/{wid}/comp",
        headers=test_wrivetedadmin_account_headers,
        json={"days": 300},
    )
    assert long_grant.status_code == 200, long_grant.text
    long_until = long_grant.json()["access_until"]

    # A shorter re-grant must not shorten the existing access.
    short_grant = client.post(
        f"/v1/admin/schools/{wid}/comp",
        headers=test_wrivetedadmin_account_headers,
        json={"days": 30},
    )
    assert short_grant.status_code == 200, short_grant.text
    assert short_grant.json()["outcome"] == "extended"
    assert short_grant.json()["access_until"] == long_until


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
        json={"days": 90},
    )
    assert resp.status_code == 409, resp.text


def test_non_staff_cannot_comp(client, session, admin_of_test_school_headers):
    school = _make_inactive_school(session)
    resp = client.post(
        f"/v1/admin/schools/{school.wriveted_identifier}/comp",
        headers=admin_of_test_school_headers,
        json={"days": 90},
    )
    assert resp.status_code in (401, 403), resp.text


@pytest.mark.asyncio
async def test_lapse_sweep_expires_staff_comp(async_session):
    """A staff comp is expired by the lapse sweep once past its expiration."""
    from app.api.internal import handle_lapse_expired_schools
    from app.services.school_access import grant_staff_comp

    school = School(
        name=f"Comp Sweep School {uuid4().hex[:8]}",
        wriveted_identifier=uuid4(),
        state=SchoolState.INACTIVE,
    )
    async_session.add(school)
    await async_session.flush()

    await grant_staff_comp(async_session, school, days=90)
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

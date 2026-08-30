import asyncio
from datetime import datetime, timedelta
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.internal import handle_lapse_expired_schools
from app.config import get_settings
from app.models.product import Product
from app.models.school import School, SchoolState
from app.models.school_billing import (
    SchoolBillingAttempt,
    SchoolBillingAttemptStatus,
    SchoolBillingMethod,
    StripeEventReceipt,
)
from app.models.subscription import Subscription, SubscriptionType
from app.services import school_billing as school_billing_module
from app.services import school_billing_status as school_billing_status_module
from app.services.school_access import (
    INVOICE_PENDING_GRANT_SOURCE,
    INVOICE_PENDING_PRODUCT_ID,
    STAFF_COMP_GRANT_SOURCE,
    STAFF_COMP_PRODUCT_ID,
    active_stripe_subscription_stmt,
    invoice_pending_grant_id,
    staff_comp_id,
)
from app.services.school_billing import create_school_checkout_session
from app.services.school_billing_status import resolve_school_billing_status
from app.services.stripe_events import process_stripe_event


@pytest.mark.asyncio
async def test_send_invoice_active_but_unpaid_is_not_paid(async_session, monkeypatch):
    _configure_stripe(monkeypatch)
    school = await _create_school(async_session)
    async_session.add(Product(id="price_invoice", name="School invoice"))
    await async_session.flush()
    async_session.add(
        Subscription(
            id="sub_invoice_unpaid",
            school_id=school.wriveted_identifier,
            type=SubscriptionType.SCHOOL,
            stripe_customer_id="cus_school_billing",
            stripe_status="active",
            collection_method="send_invoice",
            paid_at=None,
            is_active=True,
            expiration=datetime.utcnow() + timedelta(days=365),
            product_id="price_invoice",
        )
    )
    await async_session.flush()

    status = await resolve_school_billing_status(async_session, school)

    assert status.entitlement.active is False
    assert status.entitlement.source is None
    assert status.paid_subscription is None
    assert status.capabilities.card is False
    assert status.capabilities.invoice is False


@pytest.mark.asyncio
async def test_historical_checkout_preserves_access_without_claiming_payment(
    async_session, monkeypatch
):
    _configure_stripe(monkeypatch)
    school = await _create_school(async_session)
    async_session.add(Product(id="price_legacy", name="Legacy school plan"))
    await async_session.flush()
    expires_at = datetime.utcnow() + timedelta(days=30)
    async_session.add(
        Subscription(
            id=f"sub_legacy_{uuid4().hex}",
            school_id=school.wriveted_identifier,
            type=SubscriptionType.SCHOOL,
            stripe_customer_id="cus_legacy",
            latest_checkout_session_id="cs_legacy",
            stripe_status="active",
            collection_method="charge_automatically",
            paid_at=None,
            is_active=True,
            expiration=expires_at,
            product_id="price_legacy",
        )
    )
    await async_session.flush()

    status = await resolve_school_billing_status(async_session, school)

    assert status.entitlement.active is True
    assert status.entitlement.source == "legacy_subscription"
    assert status.entitlement.expires_at == expires_at
    assert status.paid_subscription is None
    assert status.capabilities.card is False
    assert status.capabilities.invoice is False


@pytest.mark.asyncio
async def test_inactive_paid_subscription_is_not_active_stripe_access(async_session):
    school = await _create_school(async_session)
    async_session.add(Product(id="price_inactive", name="Inactive school plan"))
    await async_session.flush()
    async_session.add(
        Subscription(
            id=f"sub_inactive_{uuid4().hex}",
            school_id=school.wriveted_identifier,
            type=SubscriptionType.SCHOOL,
            stripe_customer_id="cus_inactive",
            stripe_status="canceled",
            paid_at=datetime.utcnow() - timedelta(days=30),
            is_active=False,
            expiration=datetime.utcnow() + timedelta(days=30),
            product_id="price_inactive",
        )
    )
    await async_session.flush()

    active_subscription = await async_session.scalar(
        active_stripe_subscription_stmt(school.wriveted_identifier)
    )

    assert active_subscription is None


async def _create_school(async_session):
    school = School(
        name="Durable Billing School",
        country_code="ATA",
        state=SchoolState.INACTIVE,
    )
    async_session.add(school)
    await async_session.flush()
    return school


def _add_product_and_grant(
    session,
    school: School,
    *,
    product_id: str,
    grant_id: str,
    source: str,
    expiration: datetime,
    billing_attempt_id=None,
) -> Subscription:
    session.merge(Product(id=product_id, name=source))
    grant = Subscription(
        id=grant_id,
        school_id=school.wriveted_identifier,
        type=SubscriptionType.SCHOOL,
        stripe_customer_id="",
        is_active=True,
        expiration=expiration,
        product_id=product_id,
        info={
            "source": source,
            **(
                {"billing_attempt_id": str(billing_attempt_id)}
                if billing_attempt_id is not None
                else {}
            ),
        },
    )
    session.add(grant)
    return grant


def test_terminal_non_payment_persists_deactivation(session, test_school):
    now = datetime.utcnow()
    test_school.state = SchoolState.ACTIVE
    session.add(test_school)
    attempt_id = uuid4()
    session.add(
        SchoolBillingAttempt(
            id=attempt_id,
            school_id=test_school.wriveted_identifier,
            method=SchoolBillingMethod.INVOICE,
            status=SchoolBillingAttemptStatus.INVOICE_OPEN,
            client_idempotency_key="invoice-terminal",
            configured_price_id="price_invoice",
            stripe_customer_id="cus_billing",
            stripe_subscription_id="sub_unpaid",
            stripe_invoice_id="in_unpaid",
            expires_at=now + timedelta(days=44),
        )
    )
    _add_product_and_grant(
        session,
        test_school,
        product_id=INVOICE_PENDING_PRODUCT_ID,
        grant_id=invoice_pending_grant_id(test_school.wriveted_identifier),
        source=INVOICE_PENDING_GRANT_SOURCE,
        expiration=now + timedelta(days=44),
        billing_attempt_id=attempt_id,
    )
    session.commit()

    result = process_stripe_event(
        "invoice.voided",
        {
            "id": "in_unpaid",
            "object": "invoice",
            "subscription": "sub_unpaid",
        },
        event_id=f"evt_invoice_voided_{uuid4()}",
        event_created=int(now.timestamp()),
        api_version="2025-02-24.acacia",
    )

    session.expire_all()
    assert result == {"status": "processed"}
    attempt = session.execute(
        select(SchoolBillingAttempt).where(
            SchoolBillingAttempt.stripe_invoice_id == "in_unpaid"
        )
    ).scalar_one()
    assert attempt.status == SchoolBillingAttemptStatus.VOIDED
    assert session.get(School, test_school.id).state == SchoolState.INACTIVE


@pytest.mark.asyncio
async def test_expired_comp_sweep_recomputes_access(async_session):
    school = await _create_school(async_session)
    school.state = SchoolState.ACTIVE
    await async_session.merge(Product(id=STAFF_COMP_PRODUCT_ID, name="Staff comp"))
    async_session.add(
        Subscription(
            id=staff_comp_id(school.wriveted_identifier),
            school_id=school.wriveted_identifier,
            type=SubscriptionType.SCHOOL,
            stripe_customer_id="",
            is_active=True,
            expiration=datetime.utcnow() - timedelta(seconds=1),
            product_id=STAFF_COMP_PRODUCT_ID,
            info={"source": STAFF_COMP_GRANT_SOURCE},
        )
    )
    await async_session.commit()

    result = await handle_lapse_expired_schools(async_session)

    await async_session.refresh(school)
    assert result["lapsed"] == 1
    assert school.state == SchoolState.INACTIVE


@pytest.mark.asyncio
async def test_expired_paid_subscription_sweep_recomputes_access(async_session):
    school = await _create_school(async_session)
    school.state = SchoolState.ACTIVE
    await async_session.merge(Product(id="price_expired", name="Expired paid plan"))
    async_session.add(
        Subscription(
            id=f"sub_expired_{uuid4().hex}",
            school_id=school.wriveted_identifier,
            type=SubscriptionType.SCHOOL,
            stripe_customer_id="cus_expired",
            is_active=True,
            paid_at=datetime.utcnow() - timedelta(days=365),
            expiration=datetime.utcnow() - timedelta(seconds=1),
            product_id="price_expired",
            stripe_status="active",
        )
    )
    await async_session.commit()

    result = await handle_lapse_expired_schools(async_session)

    await async_session.refresh(school)
    assert result["subscriptions_expired"] == 1
    assert school.state == SchoolState.INACTIVE


@pytest.mark.asyncio
async def test_sweep_keeps_ambiguous_creating_attempt_for_review(async_session):
    school = await _create_school(async_session)
    attempt = SchoolBillingAttempt(
        id=uuid4(),
        school_id=school.wriveted_identifier,
        method=SchoolBillingMethod.CARD,
        status=SchoolBillingAttemptStatus.CREATING,
        client_idempotency_key="ambiguous-creating",
        configured_price_id="price_card",
        expires_at=datetime.utcnow() - timedelta(minutes=1),
    )
    async_session.add(attempt)
    await async_session.commit()

    result = await handle_lapse_expired_schools(async_session)

    await async_session.refresh(attempt)
    assert result["attempts_expired"] == 0
    assert attempt.status == SchoolBillingAttemptStatus.CREATING


def test_delayed_terminal_invoice_does_not_retire_newer_attempt_grant(
    session, test_school
):
    now = datetime.utcnow()
    test_school.state = SchoolState.ACTIVE
    older_attempt = SchoolBillingAttempt(
        id=uuid4(),
        school_id=test_school.wriveted_identifier,
        method=SchoolBillingMethod.INVOICE,
        status=SchoolBillingAttemptStatus.CANCELLED,
        client_idempotency_key="invoice-older",
        configured_price_id="price_invoice",
        stripe_subscription_id="sub_older",
        stripe_invoice_id="in_older",
        expires_at=now - timedelta(days=1),
    )
    newer_attempt = SchoolBillingAttempt(
        id=uuid4(),
        school_id=test_school.wriveted_identifier,
        method=SchoolBillingMethod.INVOICE,
        status=SchoolBillingAttemptStatus.INVOICE_OPEN,
        client_idempotency_key="invoice-newer",
        configured_price_id="price_invoice",
        stripe_subscription_id="sub_newer",
        stripe_invoice_id="in_newer",
        expires_at=now + timedelta(days=44),
    )
    session.add_all([older_attempt, newer_attempt])
    grant = _add_product_and_grant(
        session,
        test_school,
        product_id=INVOICE_PENDING_PRODUCT_ID,
        grant_id=invoice_pending_grant_id(test_school.wriveted_identifier),
        source=INVOICE_PENDING_GRANT_SOURCE,
        expiration=now + timedelta(days=44),
        billing_attempt_id=newer_attempt.id,
    )
    session.commit()

    process_stripe_event(
        "invoice.voided",
        {"id": "in_older", "object": "invoice", "subscription": "sub_older"},
        event_id=f"evt_old_invoice_voided_{uuid4()}",
        event_created=int(now.timestamp()),
    )

    session.expire_all()
    assert session.get(Subscription, grant.id).is_active is True
    assert session.get(SchoolBillingAttempt, newer_attempt.id).status == (
        SchoolBillingAttemptStatus.INVOICE_OPEN
    )
    assert session.get(School, test_school.id).state == SchoolState.ACTIVE


def test_duplicate_event_id_is_applied_once(session, test_school):
    now = datetime.utcnow()
    attempt = SchoolBillingAttempt(
        id=uuid4(),
        school_id=test_school.wriveted_identifier,
        method=SchoolBillingMethod.CARD,
        status=SchoolBillingAttemptStatus.CHECKOUT_OPEN,
        client_idempotency_key="duplicate-event",
        configured_price_id="price_card",
        stripe_checkout_session_id="cs_duplicate",
        checkout_url="https://checkout.test/cs_duplicate",
        expires_at=now + timedelta(hours=1),
    )
    session.add(attempt)
    session.commit()

    event_id = f"evt_duplicate_{uuid4()}"
    first = process_stripe_event(
        "checkout.session.expired",
        {"id": "cs_duplicate", "object": "checkout.session"},
        event_id=event_id,
        event_created=int(now.timestamp()),
    )
    second = process_stripe_event(
        "checkout.session.expired",
        {"id": "cs_duplicate", "object": "checkout.session"},
        event_id=event_id,
        event_created=int(now.timestamp()),
    )

    assert first == {"status": "processed"}
    assert second == {"status": "duplicate"}
    assert (
        session.scalar(
            select(func.count())
            .select_from(StripeEventReceipt)
            .where(StripeEventReceipt.event_id == event_id)
        )
        == 1
    )


def test_stale_subscription_deletion_does_not_deactivate_replacement(
    session, test_school
):
    now = datetime.utcnow()
    session.merge(Product(id="price_paid", name="Paid school"))
    old_subscription_id = f"sub_old_{uuid4().hex}"
    replacement_subscription_id = f"sub_replacement_{uuid4().hex}"
    for subscription_id, paid_at in (
        (old_subscription_id, now - timedelta(days=30)),
        (replacement_subscription_id, now - timedelta(days=1)),
    ):
        session.add(
            Subscription(
                id=subscription_id,
                school_id=test_school.wriveted_identifier,
                type=SubscriptionType.SCHOOL,
                stripe_customer_id="cus_billing",
                stripe_status="active",
                collection_method="charge_automatically",
                paid_at=paid_at,
                is_active=True,
                expiration=now + timedelta(days=300),
                product_id="price_paid",
            )
        )
    test_school.state = SchoolState.ACTIVE
    session.add(test_school)
    session.commit()

    process_stripe_event(
        "customer.subscription.deleted",
        {
            "id": old_subscription_id,
            "object": "subscription",
            "status": "canceled",
            "ended_at": int(now.timestamp()),
        },
        event_id=f"evt_old_deleted_{uuid4()}",
        event_created=int(now.timestamp()),
    )

    session.expire_all()
    assert session.get(School, test_school.id).state == SchoolState.ACTIVE
    assert session.get(Subscription, replacement_subscription_id).is_active is True


def test_customerless_checkout_expiry_marks_matching_attempt(session, test_school):
    attempt = SchoolBillingAttempt(
        id=uuid4(),
        school_id=test_school.wriveted_identifier,
        method=SchoolBillingMethod.CARD,
        status=SchoolBillingAttemptStatus.CHECKOUT_OPEN,
        client_idempotency_key="customerless",
        configured_price_id="price_card",
        stripe_checkout_session_id="cs_customerless",
        expires_at=datetime.utcnow() + timedelta(hours=1),
    )
    session.add(attempt)
    session.commit()

    process_stripe_event(
        "checkout.session.expired",
        {"id": "cs_customerless", "object": "checkout.session"},
        event_id=f"evt_customerless_{uuid4()}",
        event_created=int(datetime.utcnow().timestamp()),
    )

    session.expire_all()
    assert session.get(SchoolBillingAttempt, attempt.id).status == (
        SchoolBillingAttemptStatus.EXPIRED
    )


def test_invoice_finalization_failure_keeps_recoverable_access(session, test_school):
    now = datetime.utcnow()
    test_school.state = SchoolState.ACTIVE
    attempt = SchoolBillingAttempt(
        id=uuid4(),
        school_id=test_school.wriveted_identifier,
        method=SchoolBillingMethod.INVOICE,
        status=SchoolBillingAttemptStatus.INVOICE_OPEN,
        client_idempotency_key=f"invoice-finalization-{uuid4()}",
        configured_price_id="price_invoice",
        stripe_invoice_id=f"in_{uuid4().hex}",
        expires_at=now + timedelta(days=44),
    )
    session.add(attempt)
    _add_product_and_grant(
        session,
        test_school,
        product_id=INVOICE_PENDING_PRODUCT_ID,
        grant_id=invoice_pending_grant_id(test_school.wriveted_identifier),
        source=INVOICE_PENDING_GRANT_SOURCE,
        expiration=now + timedelta(days=44),
    )
    session.commit()

    process_stripe_event(
        "invoice.finalization_failed",
        {
            "id": attempt.stripe_invoice_id,
            "object": "invoice",
            "last_finalization_error": {"message": "Missing billing address"},
        },
        event_id=f"evt_finalization_failed_{uuid4()}",
        event_created=int(now.timestamp()),
    )

    session.expire_all()
    refreshed_attempt = session.get(SchoolBillingAttempt, attempt.id)
    pending_grant = session.get(
        Subscription, invoice_pending_grant_id(test_school.wriveted_identifier)
    )
    assert refreshed_attempt.status == SchoolBillingAttemptStatus.INVOICE_OPEN
    assert refreshed_attempt.failure_reason == "Missing billing address"
    assert pending_grant.is_active is True
    assert session.get(School, test_school.id).state == SchoolState.ACTIVE


def test_subscription_update_does_not_advance_paid_through_before_renewal_payment(
    session, test_school
):
    now = datetime.utcnow()
    paid_through = now + timedelta(days=2)
    subscription_id = f"sub_renewal_{uuid4().hex}"
    session.merge(Product(id="price_renewal", name="School renewal"))
    session.add(
        Subscription(
            id=subscription_id,
            school_id=test_school.wriveted_identifier,
            type=SubscriptionType.SCHOOL,
            stripe_customer_id=f"cus_{uuid4().hex}",
            is_active=True,
            paid_at=now - timedelta(days=363),
            expiration=paid_through,
            product_id="price_renewal",
            stripe_status="active",
            collection_method="send_invoice",
        )
    )
    test_school.state = SchoolState.ACTIVE
    session.commit()

    process_stripe_event(
        "customer.subscription.updated",
        {
            "id": subscription_id,
            "object": "subscription",
            "customer": f"cus_{uuid4().hex}",
            "status": "past_due",
            "collection_method": "send_invoice",
            "current_period_end": int((now + timedelta(days=367)).timestamp()),
            "items": {"data": [{"price": {"id": "price_renewal"}}]},
        },
        event_id=f"evt_subscription_updated_{uuid4()}",
        event_created=int(now.timestamp()),
    )

    session.expire_all()
    refreshed = session.get(Subscription, subscription_id)
    assert refreshed.expiration == paid_through
    assert refreshed.is_active is True


@patch("app.services.stripe_events.StripeSubscription.retrieve")
def test_subscription_event_rechecks_watermark_after_lock_and_preserves_it(
    retrieve_subscription, session, test_school
):
    now = datetime.utcnow().replace(microsecond=0)
    subscription_id = f"sub_watermark_{uuid4().hex}"
    session.merge(Product(id="price_watermark", name="Watermark plan"))
    subscription = Subscription(
        id=subscription_id,
        school_id=test_school.wriveted_identifier,
        type=SubscriptionType.SCHOOL,
        stripe_customer_id="cus_watermark",
        is_active=True,
        paid_at=now - timedelta(days=1),
        expiration=now + timedelta(days=30),
        product_id="price_watermark",
        stripe_status="active",
        last_stripe_event_created_at=now,
    )
    session.add(subscription)
    test_school.state = SchoolState.ACTIVE
    session.commit()
    retrieve_subscription.return_value = {
        "id": subscription_id,
        "object": "subscription",
        "customer": "cus_watermark",
        "status": "canceled",
        "current_period_end": int((now + timedelta(days=30)).timestamp()),
        "items": {"data": [{"price": {"id": "price_watermark"}}]},
    }

    process_stripe_event(
        "customer.subscription.updated",
        {
            "id": subscription_id,
            "object": "subscription",
            "status": "active",
            "items": {"data": [{"price": {"id": "price_watermark"}}]},
        },
        event_id=f"evt_equal_timestamp_{uuid4()}",
        event_created=int(now.timestamp()),
    )
    process_stripe_event(
        "customer.subscription.updated",
        {
            "id": subscription_id,
            "object": "subscription",
            "status": "active",
            "items": {"data": [{"price": {"id": "price_watermark"}}]},
        },
        event_id=f"evt_missing_timestamp_{uuid4()}",
        event_created=None,
    )

    session.expire_all()
    refreshed = session.get(Subscription, subscription_id)
    assert refreshed.stripe_status == "canceled"
    assert refreshed.is_active is False
    assert refreshed.last_stripe_event_created_at == now


def test_delayed_checkout_a_expiry_cannot_release_checkout_b(session, test_school):
    checkout_a = SchoolBillingAttempt(
        id=uuid4(),
        school_id=test_school.wriveted_identifier,
        method=SchoolBillingMethod.CARD,
        status=SchoolBillingAttemptStatus.CANCELLED,
        client_idempotency_key="checkout-a",
        configured_price_id="price_card",
        stripe_checkout_session_id="cs_a",
        expires_at=datetime.utcnow() - timedelta(hours=1),
    )
    checkout_b = SchoolBillingAttempt(
        id=uuid4(),
        school_id=test_school.wriveted_identifier,
        method=SchoolBillingMethod.CARD,
        status=SchoolBillingAttemptStatus.CHECKOUT_OPEN,
        client_idempotency_key="checkout-b",
        configured_price_id="price_card",
        stripe_checkout_session_id="cs_b",
        expires_at=datetime.utcnow() + timedelta(hours=1),
    )
    session.add_all([checkout_a, checkout_b])
    session.commit()

    process_stripe_event(
        "checkout.session.expired",
        {"id": "cs_a", "object": "checkout.session"},
        event_id=f"evt_delayed_a_{uuid4()}",
        event_created=int(datetime.utcnow().timestamp()),
    )

    session.expire_all()
    assert session.get(SchoolBillingAttempt, checkout_a.id).status == (
        SchoolBillingAttemptStatus.EXPIRED
    )
    assert session.get(SchoolBillingAttempt, checkout_b.id).status == (
        SchoolBillingAttemptStatus.CHECKOUT_OPEN
    )


@pytest.mark.asyncio
async def test_status_aggregate_precedence(async_session, monkeypatch):
    _configure_stripe(monkeypatch)
    school = await _create_school(async_session)
    now = datetime.utcnow()
    for product_id in ("price_paid", INVOICE_PENDING_PRODUCT_ID, STAFF_COMP_PRODUCT_ID):
        await async_session.merge(Product(id=product_id, name=product_id))
    paid = Subscription(
        id="sub_precedence",
        school_id=school.wriveted_identifier,
        type=SubscriptionType.SCHOOL,
        stripe_customer_id="cus_billing",
        stripe_status="active",
        collection_method="charge_automatically",
        paid_at=now,
        is_active=True,
        expiration=now + timedelta(days=365),
        product_id="price_paid",
    )
    invoice_grant = Subscription(
        id=invoice_pending_grant_id(school.wriveted_identifier),
        school_id=school.wriveted_identifier,
        type=SubscriptionType.SCHOOL,
        stripe_customer_id="",
        is_active=True,
        expiration=now + timedelta(days=40),
        product_id=INVOICE_PENDING_PRODUCT_ID,
        info={"source": INVOICE_PENDING_GRANT_SOURCE},
    )
    staff_grant = Subscription(
        id=staff_comp_id(school.wriveted_identifier),
        school_id=school.wriveted_identifier,
        type=SubscriptionType.SCHOOL,
        stripe_customer_id="",
        is_active=True,
        expiration=now + timedelta(days=90),
        product_id=STAFF_COMP_PRODUCT_ID,
        info={"source": STAFF_COMP_GRANT_SOURCE},
    )
    async_session.add_all([paid, invoice_grant, staff_grant])
    await async_session.flush()

    assert (
        await resolve_school_billing_status(async_session, school)
    ).entitlement.source == "paid_subscription"
    paid.paid_at = None
    assert (
        await resolve_school_billing_status(async_session, school)
    ).entitlement.source == INVOICE_PENDING_GRANT_SOURCE
    invoice_grant.is_active = False
    assert (
        await resolve_school_billing_status(async_session, school)
    ).entitlement.source == STAFF_COMP_GRANT_SOURCE


def _configure_stripe(monkeypatch, *, country_prices=None):
    monkeypatch.setattr(school_billing_module.settings, "STRIPE_SECRET_KEY", "sk_test")
    monkeypatch.setattr(
        school_billing_module.settings,
        "STRIPE_SCHOOL_PRICE_IDS",
        ["price_default"],
    )
    monkeypatch.setattr(
        school_billing_module.settings,
        "STRIPE_SCHOOL_PRICE_IDS_BY_COUNTRY",
        country_prices or {},
    )
    monkeypatch.setattr(
        school_billing_module.settings,
        "HUEY_BOOKS_APP_URL",
        "https://app.hueybooks.test",
    )
    monkeypatch.setattr(
        school_billing_status_module,
        "get_settings",
        lambda: school_billing_module.settings,
    )


@pytest.mark.asyncio
@patch("app.services.school_billing.stripe")
async def test_checkout_crash_retry_replays_same_stripe_object(
    mock_stripe, async_session, monkeypatch
):
    _configure_stripe(monkeypatch)
    school = await _create_school(async_session)
    await async_session.commit()
    school_id = school.id
    mock_stripe.Customer.create.return_value = Mock(id=f"cus_{uuid4().hex}")
    checkout_session_id = f"cs_{uuid4().hex}"
    mock_stripe.checkout.Session.create.return_value = Mock(
        id=checkout_session_id,
        url=f"https://checkout.test/{checkout_session_id}",
    )

    real_commit = async_session.commit
    commit_count = 0

    async def fail_final_commit_once():
        nonlocal commit_count
        commit_count += 1
        if commit_count == 3:
            await async_session.rollback()
            raise RuntimeError("simulated commit failure")
        await real_commit()

    monkeypatch.setattr(async_session, "commit", fail_final_commit_once)
    with pytest.raises(RuntimeError, match="simulated commit failure"):
        await create_school_checkout_session(
            school,
            session=async_session,
            client_idempotency_key="checkout-retry",
        )

    monkeypatch.setattr(async_session, "commit", real_commit)
    school = await async_session.get(School, school_id)
    replay = await create_school_checkout_session(
        school,
        session=async_session,
        client_idempotency_key="checkout-retry",
    )

    assert replay.checkout_url == f"https://checkout.test/{checkout_session_id}"
    assert replay.status == SchoolBillingAttemptStatus.CHECKOUT_OPEN
    assert mock_stripe.checkout.Session.create.call_count == 2
    idempotency_keys = {
        call.kwargs["idempotency_key"]
        for call in mock_stripe.checkout.Session.create.call_args_list
    }
    assert idempotency_keys == {f"{replay.attempt_id}:checkout-session"}
    assert (
        await async_session.scalar(
            select(func.count())
            .select_from(SchoolBillingAttempt)
            .where(SchoolBillingAttempt.school_id == school.wriveted_identifier)
        )
        == 1
    )


@pytest.mark.asyncio
@patch("app.services.school_billing.stripe")
async def test_concurrent_start_has_one_open_attempt(
    mock_stripe, async_session, monkeypatch
):
    _configure_stripe(monkeypatch)
    school = await _create_school(async_session)
    await async_session.commit()
    mock_stripe.Customer.create.return_value = Mock(id=f"cus_{uuid4().hex}")
    checkout_session_id = f"cs_{uuid4().hex}"
    mock_stripe.checkout.Session.create.return_value = Mock(
        id=checkout_session_id,
        url=f"https://checkout.test/{checkout_session_id}",
    )

    engine = create_async_engine(
        get_settings().SQLALCHEMY_ASYNC_URI,
        pool_size=2,
        max_overflow=0,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session_a, session_factory() as session_b:
            result_a, result_b = await asyncio.gather(
                create_school_checkout_session(
                    school,
                    session=session_a,
                    client_idempotency_key="concurrent-a",
                ),
                create_school_checkout_session(
                    school,
                    session=session_b,
                    client_idempotency_key="concurrent-b",
                ),
            )
        assert result_a.attempt_id == result_b.attempt_id
        async with session_factory() as verification_session:
            assert (
                await verification_session.scalar(
                    select(func.count())
                    .select_from(SchoolBillingAttempt)
                    .where(
                        SchoolBillingAttempt.school_id == school.wriveted_identifier,
                        SchoolBillingAttempt.status.in_(
                            {
                                SchoolBillingAttemptStatus.CREATING,
                                SchoolBillingAttemptStatus.CHECKOUT_OPEN,
                                SchoolBillingAttemptStatus.INVOICE_OPEN,
                            }
                        ),
                    )
                )
                == 1
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_lms_cannot_checkout_or_read_billing_status(
    async_client,
    test_school,
    lms_service_account_token_for_school,
):
    headers = {"Authorization": f"bearer {lms_service_account_token_for_school}"}

    checkout_response = await async_client.post(
        f"/v1/school/{test_school.wriveted_identifier}/checkout", headers=headers
    )
    status_response = await async_client.get(
        f"/v1/school/{test_school.wriveted_identifier}/billing-status",
        headers=headers,
    )

    assert checkout_response.status_code == 403
    assert status_response.status_code == 403


@pytest.mark.asyncio
@patch("app.services.school_billing.stripe")
async def test_country_price_override_is_not_publicly_controllable(
    mock_stripe,
    async_client,
    session,
    test_school,
    admin_of_test_school_headers,
    monkeypatch,
):
    _configure_stripe(monkeypatch, country_prices={"IND": "price_india"})
    test_school.country_code = "IND"
    session.add(test_school)
    session.commit()
    mock_stripe.Customer.create.return_value = Mock(id="cus_country")
    mock_stripe.checkout.Session.create.return_value = Mock(
        id="cs_country", url="https://checkout.test/cs_country"
    )

    response = await async_client.post(
        f"/v1/school/{test_school.wriveted_identifier}/checkout?price_id=price_default",
        headers=admin_of_test_school_headers,
    )

    assert response.status_code == 409, response.text
    mock_stripe.checkout.Session.create.assert_not_called()


@pytest.mark.asyncio
async def test_school_admin_cannot_patch_cached_access_state(
    async_client,
    test_school,
    admin_of_test_school_headers,
):
    response = await async_client.patch(
        f"/v1/school/{test_school.wriveted_identifier}",
        headers=admin_of_test_school_headers,
        json={"status": "active"},
    )

    assert response.status_code == 409, response.text


@pytest.mark.asyncio
@patch("app.services.school_billing.stripe")
async def test_comp_school_can_see_status_and_start_conversion(
    mock_stripe,
    async_client,
    session,
    test_school,
    admin_of_test_school_headers,
    monkeypatch,
):
    _configure_stripe(monkeypatch)
    test_school.state = SchoolState.ACTIVE
    _add_product_and_grant(
        session,
        test_school,
        product_id=STAFF_COMP_PRODUCT_ID,
        grant_id=staff_comp_id(test_school.wriveted_identifier),
        source=STAFF_COMP_GRANT_SOURCE,
        expiration=datetime.utcnow() + timedelta(days=90),
    )
    session.commit()
    mock_stripe.Customer.create.return_value = Mock(id="cus_conversion")
    mock_stripe.checkout.Session.create.return_value = Mock(
        id="cs_conversion", url="https://checkout.test/cs_conversion"
    )

    status_response = await async_client.get(
        f"/v1/school/{test_school.wriveted_identifier}/billing-status",
        headers=admin_of_test_school_headers,
    )
    checkout_response = await async_client.post(
        f"/v1/school/{test_school.wriveted_identifier}/checkout",
        headers={**admin_of_test_school_headers, "Idempotency-Key": "comp-conversion"},
    )

    assert status_response.status_code == 200, status_response.text
    assert status_response.json()["entitlement"]["source"] == STAFF_COMP_GRANT_SOURCE
    assert status_response.json()["capabilities"]["card"] is True
    assert checkout_response.status_code == 200, checkout_response.text
    assert checkout_response.json()["status"] == "checkout_open"

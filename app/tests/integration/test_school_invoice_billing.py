"""Integration tests for the automated school invoice (net-terms) billing flow.

Covers the end-to-end lifecycle with simulated Stripe payloads:
- creating an invoice subscription issues an ``invoice_pending`` comp grant and
  activates the school immediately (net terms), reusing an existing Stripe
  Customer rather than duplicating it, and refusing a second live subscription;
- ``invoice.paid`` upserts an API-created (invoice) subscription that never had a
  checkout, activates the school, and retires its comp grants (invoice_pending
  and staff_comp), idempotently on redelivery;
- schools resolve from ``metadata.wriveted_school_id`` when there is no
  ``client_reference_id`` (invoice/subscription events);
- the never-paid branch: the subscription is cancelled but a live grant holds
  access, then the lapse sweep drops the school to INACTIVE once the grant
  expires;
- the billing portal returns a URL only for a real Stripe subscription (else
  404 via a ``None`` from the service).
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, Mock, patch
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.product import Product
from app.models.school import School, SchoolState
from app.models.school_billing import (
    SchoolBillingAccount,
    SchoolBillingAttempt,
    SchoolBillingAttemptStatus,
    SchoolBillingMethod,
)
from app.models.subscription import Subscription, SubscriptionType
from app.repositories.school_repository import school_repository
from app.repositories.subscription_repository import subscription_repository
from app.services import school_billing as school_billing_module
from app.services.school_access import (
    INVOICE_PENDING_GRANT_SOURCE,
    INVOICE_PENDING_PRODUCT_ID,
    STAFF_COMP_GRANT_SOURCE,
    STAFF_COMP_PRODUCT_ID,
    SchoolNotFoundError,
    deactivate_school_on_non_payment_sync,
    invoice_pending_grant_id,
    staff_comp_id,
)
from app.services.school_billing import (
    SchoolBillingConflictError,
    create_school_billing_portal_session,
    create_school_checkout_session,
    create_school_invoice_subscription,
)
from app.services.stripe_events import (
    _handle_invoice_paid,
    _handle_subscription_cancelled,
)

INVOICE_PRICE_ID = "price_invoice_school"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _mock_invoice_subscription(
    mock_sub,
    *,
    school_id,
    price_id=INVOICE_PRICE_ID,
    status="active",
    customer="cus_invoice",
    period_end=1893456000,
    collection_method="send_invoice",
):
    """A MagicMock standing in for a retrieved Stripe Subscription."""
    sub = MagicMock()
    sub.status = status
    sub.current_period_end = period_end
    sub.customer = customer
    sub.__getitem__.side_effect = lambda key: {
        "items": {"data": [{"price": {"id": price_id}}]}
    }[key]
    sub.get.side_effect = lambda key, default=None: {
        "metadata": {"wriveted_school_id": str(school_id)},
        "collection_method": collection_method,
    }.get(key, default)
    mock_sub.retrieve.return_value = sub
    return sub


def _invoice_event(
    school_id,
    *,
    invoice_id="in_1",
    subscription_id="sub_invoice",
    customer="cus_invoice",
    parent_shape=False,
) -> dict:
    event = {
        "id": invoice_id,
        "object": "invoice",
        "customer": customer,
        "hosted_invoice_url": "https://pay.stripe.test/i/in_1",
        "amount_due": 8000,
        "collection_method": "send_invoice",
    }
    if parent_shape:
        # 2025+ API version shape.
        event["parent"] = {"subscription_details": {"subscription": subscription_id}}
    else:
        event["subscription"] = subscription_id
    return event


def _seed_invoice_school(session, test_school, *, with_staff_comp=True):
    """Put the school in the net-terms state: PENDING with an invoice_pending
    grant (and optionally a staff_comp grant), no real Stripe subscription yet."""
    test_school.state = SchoolState.PENDING
    test_school.info = {
        **(test_school.info or {}),
        "onboarding": {"contact_email": "bursar@school.example", "contact_name": "Pat"},
    }
    session.add(test_school)
    session.merge(Product(id=INVOICE_PRICE_ID, name="School (invoice)"))
    session.merge(Product(id=INVOICE_PENDING_PRODUCT_ID, name="Invoice pending"))
    session.merge(Product(id=STAFF_COMP_PRODUCT_ID, name="Staff comp"))
    session.flush()

    wid = test_school.wriveted_identifier
    session.add(
        Subscription(
            id=invoice_pending_grant_id(wid),
            school_id=wid,
            type=SubscriptionType.SCHOOL,
            stripe_customer_id="",
            is_active=True,
            expiration=datetime.utcnow() + timedelta(days=44),
            product_id=INVOICE_PENDING_PRODUCT_ID,
            info={"source": INVOICE_PENDING_GRANT_SOURCE},
        )
    )
    if with_staff_comp:
        session.add(
            Subscription(
                id=staff_comp_id(wid),
                school_id=wid,
                type=SubscriptionType.SCHOOL,
                stripe_customer_id="",
                is_active=True,
                expiration=datetime.utcnow() + timedelta(days=90),
                product_id=STAFF_COMP_PRODUCT_ID,
                info={"source": STAFF_COMP_GRANT_SOURCE},
            )
        )
    session.commit()


def _active_subscriptions(session, wid):
    return (
        session.execute(
            select(Subscription).where(
                Subscription.school_id == wid,
                Subscription.is_active.is_(True),
            )
        )
        .scalars()
        .all()
    )


# --------------------------------------------------------------------------- #
# invoice.paid: upsert API-created subscription, activate, retire comps
# --------------------------------------------------------------------------- #


@patch("app.services.stripe_events.StripeSubscription")
def test_invoice_paid_activates_school_and_retires_comps(
    mock_sub, session, test_school
):
    _seed_invoice_school(session, test_school)
    wid = test_school.wriveted_identifier
    _mock_invoice_subscription(mock_sub, school_id=wid)

    # No client_reference_id / no school passed: must resolve from subscription
    # metadata (the core webhook-hardening path for invoice subscriptions).
    _handle_invoice_paid(session, None, None, _invoice_event(wid))

    session.expire_all()
    school = school_repository.get_by_wriveted_id(session, str(wid))
    assert school.state == SchoolState.ACTIVE

    active = _active_subscriptions(session, wid)
    # Exactly one active row remains — the paying invoice subscription.
    assert len(active) == 1
    assert active[0].id == "sub_invoice"
    assert active[0].stripe_customer_id == "cus_invoice"
    # The upserted (API-created) subscription stores the CONVERTED naive datetime,
    # not the raw unix timestamp int.
    assert active[0].expiration == datetime.utcfromtimestamp(1893456000)

    # Both comp grants are retired (survive as rows, flipped inactive).
    grant = subscription_repository.get_by_id(session, invoice_pending_grant_id(wid))
    staff = subscription_repository.get_by_id(session, staff_comp_id(wid))
    assert grant.is_active is False
    assert staff.is_active is False


@patch("app.services.stripe_events.StripeSubscription")
def test_invoice_paid_reads_parent_subscription_shape(mock_sub, session, test_school):
    """The 2025+ API version nests the subscription id under invoice.parent."""
    _seed_invoice_school(session, test_school, with_staff_comp=False)
    wid = test_school.wriveted_identifier
    _mock_invoice_subscription(mock_sub, school_id=wid)

    _handle_invoice_paid(session, None, None, _invoice_event(wid, parent_shape=True))

    mock_sub.retrieve.assert_called_once_with("sub_invoice")
    session.expire_all()
    school = school_repository.get_by_wriveted_id(session, str(wid))
    assert school.state == SchoolState.ACTIVE


@patch("app.services.stripe_events.StripeSubscription")
def test_invoice_paid_is_idempotent_on_redelivery(mock_sub, session, test_school):
    _seed_invoice_school(session, test_school)
    wid = test_school.wriveted_identifier
    _mock_invoice_subscription(mock_sub, school_id=wid)

    _handle_invoice_paid(session, None, None, _invoice_event(wid))
    session.expire_all()
    first_active_ids = {s.id for s in _active_subscriptions(session, wid)}

    # Stripe redelivers the same invoice.paid.
    _handle_invoice_paid(session, None, None, _invoice_event(wid))
    session.expire_all()

    school = school_repository.get_by_wriveted_id(session, str(wid))
    assert school.state == SchoolState.ACTIVE
    second_active_ids = {s.id for s in _active_subscriptions(session, wid)}
    assert second_active_ids == first_active_ids == {"sub_invoice"}


@patch("app.services.stripe_events.StripeCustomer")
@patch("app.services.stripe_events.StripeSubscription")
def test_send_invoice_subscription_created_does_not_retire_pending_grant(
    mock_sub, mock_customer, session, test_school
):
    """A ``send_invoice`` subscription is ``active`` pre-payment and carries
    ``metadata.wriveted_school_id``. ``customer.subscription.created`` must NOT
    retire the ``invoice_pending`` grant while the invoice is unpaid — otherwise
    the never-paid lapse backstop is defeated. Only ``invoice.paid``
    retires it.
    """
    from app.services.stripe_events import (
        _handle_invoice_paid,
        _handle_subscription_created,
    )

    _seed_invoice_school(session, test_school, with_staff_comp=False)
    wid = test_school.wriveted_identifier
    school = school_repository.get_by_wriveted_id(session, str(wid))
    school.state = SchoolState.ACTIVE
    session.add(school)
    session.commit()

    # The retrieved Stripe Customer has no wriveted user id in metadata.
    cust = MagicMock()
    cust.__getitem__.side_effect = lambda key: {"metadata": {}}[key]
    mock_customer.retrieve.return_value = cust

    created_event = {
        "id": "sub_invoice",
        "object": "subscription",
        "status": "active",  # send_invoice subs are active before payment
        "current_period_end": 1893456000,
        "customer": "cus_invoice",
        "collection_method": "send_invoice",
        "latest_invoice": {"status": "open"},  # unpaid
        "items": {"data": [{"price": {"id": INVOICE_PRICE_ID}}]},
        "metadata": {"wriveted_school_id": str(wid)},
    }
    _handle_subscription_created(session, None, school, created_event)
    session.expire_all()

    # The pending grant SURVIVES and the school stays ACTIVE.
    grant = subscription_repository.get_by_id(session, invoice_pending_grant_id(wid))
    assert grant.is_active is True
    school = school_repository.get_by_wriveted_id(session, str(wid))
    assert school.state == SchoolState.ACTIVE

    # Now the invoice is actually paid → the grant is retired.
    _mock_invoice_subscription(mock_sub, school_id=wid)
    _handle_invoice_paid(session, None, None, _invoice_event(wid))
    session.expire_all()

    grant = subscription_repository.get_by_id(session, invoice_pending_grant_id(wid))
    assert grant.is_active is False
    school = school_repository.get_by_wriveted_id(session, str(wid))
    assert school.state == SchoolState.ACTIVE


# --------------------------------------------------------------------------- #
# never-paid failure branch
# --------------------------------------------------------------------------- #


def test_cancelled_invoice_sub_keeps_school_active_until_grant_lapses(
    session, test_school
):
    """A never-paid invoice subscription is cancelled by Stripe, but the live
    invoice_pending grant keeps the school ACTIVE until the sweep lapses it."""
    _seed_invoice_school(session, test_school, with_staff_comp=False)
    wid = test_school.wriveted_identifier
    # The school was made ACTIVE by the grant; reflect that.
    school = school_repository.get_by_wriveted_id(session, str(wid))
    school.state = SchoolState.ACTIVE
    session.add(school)
    # A real (invoice) Stripe subscription row exists, then gets cancelled.
    session.add(
        Subscription(
            id="sub_invoice_unpaid",
            school_id=wid,
            type=SubscriptionType.SCHOOL,
            stripe_customer_id="cus_invoice",
            is_active=True,
            expiration=datetime.utcnow() + timedelta(days=30),
            product_id=INVOICE_PRICE_ID,
        )
    )
    session.commit()

    _handle_subscription_cancelled(
        session,
        None,
        None,
        {"id": "sub_invoice_unpaid", "object": "subscription", "ended_at": 1893456000},
    )

    session.expire_all()
    school = school_repository.get_by_wriveted_id(session, str(wid))
    # Grant is still live, so the school stays ACTIVE despite the cancellation.
    assert school.state == SchoolState.ACTIVE
    cancelled = subscription_repository.get_by_id(session, "sub_invoice_unpaid")
    assert cancelled.is_active is False


@pytest.mark.asyncio
async def test_lapse_sweep_expires_invoice_pending_grant(async_session):
    """invoice_pending is a COMP_GRANT_SOURCE, so the sweep lapses a school whose
    net-terms window elapsed with no payment."""
    from app.api.internal import handle_lapse_expired_schools

    await async_session.merge(
        Product(id=INVOICE_PENDING_PRODUCT_ID, name="Invoice pending")
    )
    await async_session.flush()

    school = School(
        name=f"Invoice Sweep {uuid4().hex[:8]}",
        wriveted_identifier=uuid4(),
        state=SchoolState.ACTIVE,
    )
    async_session.add(school)
    await async_session.flush()
    async_session.add(
        Subscription(
            id=invoice_pending_grant_id(school.wriveted_identifier),
            school_id=school.wriveted_identifier,
            type=SubscriptionType.SCHOOL,
            stripe_customer_id="",
            is_active=True,
            expiration=datetime.utcnow() - timedelta(days=1),
            product_id=INVOICE_PENDING_PRODUCT_ID,
            info={"source": INVOICE_PENDING_GRANT_SOURCE},
        )
    )
    await async_session.commit()

    result = await handle_lapse_expired_schools(async_session)

    await async_session.refresh(school)
    assert school.state == SchoolState.INACTIVE
    assert result["lapsed"] >= 1


# --------------------------------------------------------------------------- #
# create_school_invoice_subscription service
# --------------------------------------------------------------------------- #


def _configure_billing_settings(monkeypatch):
    from app.services import school_billing
    from app.services import school_billing_status as school_billing_status_module
    from app.services.stripe_price_cache import StripePriceInfo

    monkeypatch.setattr(school_billing.settings, "STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setattr(
        school_billing.settings, "STRIPE_SCHOOL_PRICE_IDS", [INVOICE_PRICE_ID]
    )
    monkeypatch.setattr(
        school_billing.settings, "STRIPE_SCHOOL_PRICE_IDS_BY_COUNTRY", {}
    )
    monkeypatch.setattr(school_billing.settings, "INVOICE_DAYS_UNTIL_DUE", 30)
    monkeypatch.setattr(school_billing.settings, "INVOICE_PENDING_GRACE_DAYS", 14)

    price_info = StripePriceInfo(
        unit_amount=24000, currency="aud", interval="year", interval_count=1
    )

    async def _fake_get_price_info(price_id, **kwargs):
        return price_info

    monkeypatch.setattr(
        school_billing_status_module, "get_price_info", _fake_get_price_info
    )
    monkeypatch.setattr(
        school_billing_status_module,
        "get_price_info_sync",
        lambda price_id, **kwargs: price_info,
    )


async def _new_school(async_session, *, country_code="ATA") -> School:
    school = School(
        name=f"Invoice School {uuid4().hex[:8]}",
        wriveted_identifier=uuid4(),
        country_code=country_code,
        state=SchoolState.INACTIVE,
    )
    async_session.add(school)
    await async_session.flush()
    return school


@pytest.mark.asyncio
@patch("app.services.school_billing.stripe")
async def test_create_invoice_subscription_grants_access_and_calls_stripe(
    mock_stripe, async_session, monkeypatch
):
    _configure_billing_settings(monkeypatch)
    school = await _new_school(async_session)

    customer_id = f"cus_{uuid4().hex}"
    mock_stripe.Customer.create.return_value = Mock(id=customer_id)
    sub_obj = MagicMock()
    sub_obj.id = "sub_new"
    sub_obj.get.return_value = {
        "id": "in_new",
        "hosted_invoice_url": "https://pay.stripe.test/i/x",
    }
    mock_stripe.Subscription.create.return_value = sub_obj

    before = datetime.utcnow()
    result = await create_school_invoice_subscription(
        async_session,
        school,
        billing_email="bursar@school.example",
        billing_name="School Bursar",
        po_number="PO-12345",
    )

    assert result.status == SchoolBillingAttemptStatus.INVOICE_OPEN
    assert result.hosted_invoice_url == "https://pay.stripe.test/i/x"

    # Customer creation is dedicated to school billing; PO data is not stored in
    # Customer defaults where it could leak to a later invoice.
    mock_stripe.Customer.create.assert_called_once()
    _, cust_kwargs = mock_stripe.Customer.create.call_args
    assert cust_kwargs["metadata"]["wriveted_school_id"] == str(
        school.wriveted_identifier
    )
    assert "school_billing_attempt_id" not in cust_kwargs["metadata"]
    assert cust_kwargs["idempotency_key"] == (
        f"{result.attempt_id}:customer-create"
    )
    assert "invoice_settings" not in cust_kwargs
    mock_stripe.Invoice.modify.assert_called_once_with(
        "in_new",
        custom_fields=[{"name": "PO number", "value": "PO-12345"}],
        idempotency_key=f"{result.attempt_id}:invoice-po",
    )

    # Subscription created as a send_invoice net-terms sub.
    _, sub_kwargs = mock_stripe.Subscription.create.call_args
    assert sub_kwargs["customer"] == customer_id
    assert sub_kwargs["collection_method"] == "send_invoice"
    assert sub_kwargs["days_until_due"] == 30
    assert sub_kwargs["items"] == [{"price": INVOICE_PRICE_ID}]
    assert sub_kwargs["metadata"]["wriveted_school_id"] == str(
        school.wriveted_identifier
    )
    assert sub_kwargs["metadata"]["purchase_order_number"] == "PO-12345"
    assert sub_kwargs["idempotency_key"] == (
        f"{result.attempt_id}:invoice-subscription"
    )

    # School is ACTIVE via a fresh invoice_pending grant (~44 days out).
    await async_session.refresh(school)
    assert school.state == SchoolState.ACTIVE
    grant = (
        await async_session.execute(
            select(Subscription).where(
                Subscription.id == invoice_pending_grant_id(school.wriveted_identifier)
            )
        )
    ).scalar_one()
    assert grant.is_active
    assert grant.info["source"] == INVOICE_PENDING_GRANT_SOURCE
    assert 43 <= (grant.expiration - before).days <= 45


@pytest.mark.asyncio
@patch("app.services.school_billing.stripe")
async def test_create_invoice_subscription_does_not_reuse_historical_customer(
    mock_stripe, async_session, monkeypatch
):
    _configure_billing_settings(monkeypatch)
    school = await _new_school(async_session)
    # A prior (now inactive) subscription carries the known Stripe customer id.
    await _price_product(async_session)
    historical_subscription_id = f"sub_old_{uuid4().hex}"
    async_session.add(
        Subscription(
            id=historical_subscription_id,
            school_id=school.wriveted_identifier,
            type=SubscriptionType.SCHOOL,
            stripe_customer_id="cus_existing",
            is_active=False,
            expiration=datetime.utcnow() - timedelta(days=5),
            product_id=INVOICE_PRICE_ID,
        )
    )
    await async_session.flush()

    sub_obj = MagicMock()
    sub_obj.id = "sub_new"
    sub_obj.get.return_value = None  # no latest_invoice expanded
    mock_stripe.Subscription.create.return_value = sub_obj
    dedicated_customer_id = f"cus_{uuid4().hex}"
    mock_stripe.Customer.create.return_value = Mock(id=dedicated_customer_id)

    result = await create_school_invoice_subscription(
        async_session,
        school,
        billing_email="new-payer@school.example",
        billing_name="New Payer",
        po_number="PO-42",
    )

    # Historical/sponsor customer ids are never reused for the school portal.
    mock_stripe.Customer.create.assert_called_once()
    mock_stripe.Customer.modify.assert_called_once()
    modify_args, modify_kwargs = mock_stripe.Customer.modify.call_args
    assert modify_args[0] == dedicated_customer_id
    assert modify_kwargs["email"] == "new-payer@school.example"
    assert modify_kwargs["name"] == "New Payer"
    assert "invoice_settings" not in modify_kwargs
    _, sub_kwargs = mock_stripe.Subscription.create.call_args
    assert sub_kwargs["customer"] == dedicated_customer_id
    assert result.hosted_invoice_url is None


async def _price_product(async_session) -> Product:
    return await async_session.merge(
        Product(id=INVOICE_PRICE_ID, name="School (invoice)")
    )


@pytest.mark.asyncio
@patch("app.services.school_billing.stripe")
async def test_create_invoice_subscription_conflicts_with_live_subscription(
    mock_stripe, async_session, monkeypatch
):
    _configure_billing_settings(monkeypatch)
    school = await _new_school(async_session)
    await _price_product(async_session)
    async_session.add(
        Subscription(
            id="sub_live",
            school_id=school.wriveted_identifier,
            type=SubscriptionType.SCHOOL,
            stripe_customer_id="cus_live",
            is_active=True,
            expiration=datetime.utcnow() + timedelta(days=30),
            product_id=INVOICE_PRICE_ID,
        )
    )
    await async_session.flush()

    with pytest.raises(SchoolBillingConflictError):
        await create_school_invoice_subscription(
            async_session, school, billing_email="bursar@school.example"
        )
    mock_stripe.Subscription.create.assert_not_called()


@pytest.mark.asyncio
@patch("app.services.school_billing.stripe")
async def test_create_invoice_subscription_second_call_replays_no_double_bill(
    mock_stripe, async_session, monkeypatch
):
    """Two sequential creates for the same school: the first issues the invoice
    and the invoice_pending grant; the second must detect the live grant (empty
    customer id, invisible to the Stripe-only predicate) and 409 WITHOUT a second
    Stripe Subscription.create — no double-bill."""
    _configure_billing_settings(monkeypatch)
    school = await _new_school(async_session)

    mock_stripe.Customer.create.return_value = Mock(id=f"cus_{uuid4().hex}")
    sub_obj = MagicMock()
    sub_obj.id = "sub_new"
    sub_obj.get.return_value = {"hosted_invoice_url": "https://pay.stripe.test/i/x"}
    mock_stripe.Subscription.create.return_value = sub_obj

    first = await create_school_invoice_subscription(
        async_session, school, billing_email="bursar@school.example"
    )
    assert first.status == SchoolBillingAttemptStatus.INVOICE_OPEN

    second = await create_school_invoice_subscription(
        async_session, school, billing_email="bursar@school.example"
    )
    assert second.attempt_id == first.attempt_id

    # Stripe was hit exactly once across both calls.
    mock_stripe.Subscription.create.assert_called_once()
    mock_stripe.Customer.create.assert_called_once()

    # A deterministic idempotency key is passed so even a retry that DID reach
    # Stripe would not create a duplicate.
    _, sub_kwargs = mock_stripe.Subscription.create.call_args
    assert sub_kwargs["idempotency_key"] == (
        f"{first.attempt_id}:invoice-subscription"
    )


@pytest.mark.asyncio
@patch("app.services.school_billing.stripe")
async def test_resume_invoice_attempt_uses_persisted_request_details(
    mock_stripe, async_session, monkeypatch
):
    """A reload may submit a new client key while resuming the open attempt."""
    _configure_billing_settings(monkeypatch)
    school = await _new_school(async_session)
    attempt = SchoolBillingAttempt(
        school_id=school.wriveted_identifier,
        method=SchoolBillingMethod.INVOICE,
        status=SchoolBillingAttemptStatus.CREATING,
        client_idempotency_key="original-tab",
        configured_price_id=INVOICE_PRICE_ID,
        billing_email="original@school.example",
        billing_name="Original Bursar",
        purchase_order_number="PO-ORIGINAL",
        invoice_days_until_due=30,
        expires_at=datetime.utcnow() + timedelta(days=1),
    )
    async_session.add(attempt)
    await async_session.commit()

    mock_stripe.Customer.create.return_value = Mock(id=f"cus_{uuid4().hex}")
    subscription = MagicMock()
    subscription.id = f"sub_{uuid4().hex}"
    subscription.get.return_value = None
    mock_stripe.Subscription.create.return_value = subscription

    result = await create_school_invoice_subscription(
        async_session,
        school,
        billing_email="changed@school.example",
        billing_name="Changed Name",
        po_number="PO-CHANGED",
        client_idempotency_key="new-tab",
    )

    assert result.attempt_id == attempt.id
    _, customer_kwargs = mock_stripe.Customer.modify.call_args
    assert customer_kwargs["email"] == "original@school.example"
    assert customer_kwargs["name"] == "Original Bursar"
    _, subscription_kwargs = mock_stripe.Subscription.create.call_args
    assert subscription_kwargs["metadata"]["purchase_order_number"] == "PO-ORIGINAL"


@pytest.mark.asyncio
@patch("app.services.school_billing.stripe")
async def test_resume_invoice_uses_persisted_terms_for_access_expiry(
    mock_stripe, async_session, monkeypatch
):
    _configure_billing_settings(monkeypatch)
    school = await _new_school(async_session)
    attempt = SchoolBillingAttempt(
        school_id=school.wriveted_identifier,
        method=SchoolBillingMethod.INVOICE,
        status=SchoolBillingAttemptStatus.CREATING,
        client_idempotency_key="persisted-terms",
        configured_price_id=INVOICE_PRICE_ID,
        billing_email="bursar@school.example",
        invoice_days_until_due=30,
        expires_at=datetime.utcnow() + timedelta(hours=1),
    )
    async_session.add(attempt)
    await async_session.commit()
    monkeypatch.setattr(school_billing_module.settings, "INVOICE_DAYS_UNTIL_DUE", 60)

    mock_stripe.Customer.create.return_value = Mock(id=f"cus_{uuid4().hex}")
    subscription = MagicMock()
    subscription.id = f"sub_{uuid4().hex}"
    subscription.get.return_value = None
    mock_stripe.Subscription.create.return_value = subscription
    before = datetime.utcnow()

    await create_school_invoice_subscription(
        async_session,
        school,
        billing_email="bursar@school.example",
        client_idempotency_key="persisted-terms",
    )

    await async_session.refresh(attempt)
    assert before + timedelta(days=43) < attempt.expires_at
    assert attempt.expires_at < before + timedelta(days=45)
    grant = await async_session.get(
        Subscription, invoice_pending_grant_id(school.wriveted_identifier)
    )
    assert grant.info["billing_attempt_id"] == str(attempt.id)


@pytest.mark.asyncio
@patch("app.services.school_billing.stripe")
async def test_expired_creating_attempt_requires_review_without_retrying_stripe(
    mock_stripe, async_session, monkeypatch
):
    _configure_billing_settings(monkeypatch)
    school = await _new_school(async_session)
    attempt = SchoolBillingAttempt(
        school_id=school.wriveted_identifier,
        method=SchoolBillingMethod.INVOICE,
        status=SchoolBillingAttemptStatus.CREATING,
        client_idempotency_key="ambiguous-stripe-result",
        configured_price_id=INVOICE_PRICE_ID,
        billing_email="bursar@school.example",
        invoice_days_until_due=30,
        expires_at=datetime.utcnow() - timedelta(minutes=1),
    )
    async_session.add(attempt)
    await async_session.commit()

    with pytest.raises(SchoolBillingConflictError, match="requires staff review"):
        await create_school_invoice_subscription(
            async_session,
            school,
            billing_email="bursar@school.example",
            client_idempotency_key="ambiguous-stripe-result",
        )

    mock_stripe.Customer.create.assert_not_called()
    mock_stripe.Subscription.create.assert_not_called()


@pytest.mark.asyncio
@patch("app.services.school_billing.stripe")
async def test_create_invoice_subscription_new_attempt_uses_fresh_idempotency_key(
    mock_stripe, async_session, monkeypatch
):
    """A new attempt after a terminal subscription gets a fresh operation key."""
    _configure_billing_settings(monkeypatch)
    school = await _new_school(async_session)
    await _price_product(async_session)
    # A prior invoice subscription that was created then cancelled/voided: a real
    # (non-empty customer) Stripe sub, now inactive. Nothing live remains.
    async_session.add(
        Subscription(
            id=f"sub_dead_{uuid4().hex}",
            school_id=school.wriveted_identifier,
            type=SubscriptionType.SCHOOL,
            stripe_customer_id="cus_existing",
            is_active=False,
            expiration=datetime.utcnow() - timedelta(days=1),
            product_id=INVOICE_PRICE_ID,
        )
    )
    await async_session.flush()

    sub_obj = MagicMock()
    sub_obj.id = "sub_retry"
    sub_obj.get.return_value = {"hosted_invoice_url": "https://pay.stripe.test/i/y"}
    mock_stripe.Subscription.create.return_value = sub_obj
    mock_stripe.Customer.create.return_value = Mock(id=f"cus_{uuid4().hex}")

    await create_school_invoice_subscription(
        async_session, school, billing_email="bursar@school.example"
    )

    _, sub_kwargs = mock_stripe.Subscription.create.call_args
    assert sub_kwargs["idempotency_key"]


@pytest.mark.asyncio
@patch("app.services.school_billing.stripe")
async def test_create_invoice_subscription_missing_school_404_before_stripe(
    mock_stripe, async_session, monkeypatch
):
    """A school that has vanished (no row to lock) must be rejected BEFORE any
    Stripe call, so no invoice is ever emitted for a non-existent school."""
    _configure_billing_settings(monkeypatch)
    # A detached School never persisted: the lock SELECT finds no row.
    ghost = School(
        name="Ghost School",
        wriveted_identifier=uuid4(),
        country_code="ATA",
        state=SchoolState.INACTIVE,
    )

    with pytest.raises(SchoolNotFoundError):
        await create_school_invoice_subscription(
            async_session, ghost, billing_email="bursar@school.example"
        )

    mock_stripe.Customer.create.assert_not_called()
    mock_stripe.Subscription.create.assert_not_called()


# --------------------------------------------------------------------------- #
# card checkout: cross-path double-bill guard
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@patch("app.services.school_billing.stripe")
async def test_checkout_conflicts_with_live_invoice_pending_grant(
    mock_stripe, async_session, monkeypatch
):
    """A school that just requested an invoice (live invoice_pending grant + open
    send_invoice Stripe sub) must NOT be able to start a card Checkout: the
    checkout path shares the invoice path's live-subscription guard, so it 409s
    instead of leaving two collectible obligations."""
    _configure_billing_settings(monkeypatch)
    monkeypatch.setattr(
        school_billing_module.settings,
        "HUEY_BOOKS_APP_URL",
        "https://app.hueybooks.test",
    )
    school = await _new_school(async_session)
    await async_session.merge(
        Product(id=INVOICE_PENDING_PRODUCT_ID, name="Invoice pending")
    )
    await async_session.flush()
    async_session.add(
        Subscription(
            id=invoice_pending_grant_id(school.wriveted_identifier),
            school_id=school.wriveted_identifier,
            type=SubscriptionType.SCHOOL,
            stripe_customer_id="",
            is_active=True,
            expiration=datetime.utcnow() + timedelta(days=44),
            product_id=INVOICE_PENDING_PRODUCT_ID,
            info={"source": INVOICE_PENDING_GRANT_SOURCE},
        )
    )
    await async_session.flush()

    with pytest.raises(SchoolBillingConflictError):
        await create_school_checkout_session(school, session=async_session)

    mock_stripe.checkout.Session.create.assert_not_called()


@pytest.mark.asyncio
@patch("app.services.school_billing.stripe")
async def test_checkout_clean_school_returns_url(
    mock_stripe, async_session, monkeypatch
):
    """A clean school (no live subscription) still gets a card Checkout URL."""
    _configure_billing_settings(monkeypatch)
    monkeypatch.setattr(
        school_billing_module.settings,
        "HUEY_BOOKS_APP_URL",
        "https://app.hueybooks.test",
    )
    school = await _new_school(async_session)

    mock_stripe.Customer.create.return_value = Mock(id=f"cus_{uuid4().hex}")
    checkout_session_id = f"cs_{uuid4().hex}"
    mock_stripe.checkout.Session.create.return_value = Mock(
        id=checkout_session_id,
        url=f"https://checkout.stripe.test/c/{checkout_session_id}",
    )

    result = await create_school_checkout_session(school, session=async_session)

    assert result.checkout_url == f"https://checkout.stripe.test/c/{checkout_session_id}"
    mock_stripe.checkout.Session.create.assert_called_once()


# --------------------------------------------------------------------------- #
# billing portal
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@patch("app.services.school_billing.stripe")
async def test_billing_portal_returns_url_for_real_subscription(
    mock_stripe, async_session, monkeypatch
):
    _configure_billing_settings(monkeypatch)
    school = await _new_school(async_session)
    await _price_product(async_session)
    async_session.add(
        Subscription(
            id="sub_portal",
            school_id=school.wriveted_identifier,
            type=SubscriptionType.SCHOOL,
            stripe_customer_id="cus_portal",
            is_active=True,
            paid_at=datetime.utcnow(),
            expiration=datetime.utcnow() + timedelta(days=30),
            product_id=INVOICE_PRICE_ID,
        )
    )
    async_session.add(
        SchoolBillingAccount(
            school_id=school.wriveted_identifier,
            stripe_customer_id="cus_portal",
        )
    )
    await async_session.flush()

    mock_stripe.billing_portal.Session.create.return_value = Mock(
        url="https://billing.stripe.test/session/xyz"
    )

    url = await create_school_billing_portal_session(async_session, school)
    assert url == "https://billing.stripe.test/session/xyz"
    _, kwargs = mock_stripe.billing_portal.Session.create.call_args
    assert kwargs["customer"] == "cus_portal"


@pytest.mark.asyncio
@patch("app.services.school_billing.stripe")
async def test_billing_portal_none_without_real_subscription(
    mock_stripe, async_session, monkeypatch
):
    _configure_billing_settings(monkeypatch)
    school = await _new_school(async_session)
    # Only a comped grant (empty stripe_customer_id) — not a portal-eligible sub.
    await async_session.merge(
        Product(id=INVOICE_PENDING_PRODUCT_ID, name="Invoice pending")
    )
    await async_session.flush()
    async_session.add(
        Subscription(
            id=invoice_pending_grant_id(school.wriveted_identifier),
            school_id=school.wriveted_identifier,
            type=SubscriptionType.SCHOOL,
            stripe_customer_id="",
            is_active=True,
            expiration=datetime.utcnow() + timedelta(days=30),
            product_id=INVOICE_PENDING_PRODUCT_ID,
            info={"source": INVOICE_PENDING_GRANT_SOURCE},
        )
    )
    await async_session.flush()

    url = await create_school_billing_portal_session(async_session, school)
    assert url is None
    mock_stripe.billing_portal.Session.create.assert_not_called()


# --------------------------------------------------------------------------- #
# endpoint: transaction commit + billing authorization
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@patch("app.services.school_billing.stripe")
async def test_invoice_subscription_endpoint_commits_grant_and_active_state(
    mock_stripe,
    async_client,
    session,
    test_school,
    admin_of_test_school_headers,
    monkeypatch,
):
    """The endpoint must COMMIT: Stripe already emitted the (irreversible)
    invoice, so the invoice_pending grant + ACTIVE flip have to persist. Proven
    by reading through a SEPARATE session after the request."""
    _configure_billing_settings(monkeypatch)

    # Start the school PENDING so the ACTIVE flip is observable.
    wid = test_school.wriveted_identifier
    school = school_repository.get_by_wriveted_id(session, str(wid))
    school.state = SchoolState.PENDING
    session.add(school)
    session.commit()

    mock_stripe.Customer.create.return_value = Mock(id=f"cus_{uuid4().hex}")
    sub_obj = MagicMock()
    sub_obj.id = "sub_ep"
    sub_obj.get.return_value = {"hosted_invoice_url": "https://pay.stripe.test/i/ep"}
    mock_stripe.Subscription.create.return_value = sub_obj

    resp = await async_client.post(
        f"/v1/school/{wid}/invoice-subscription",
        json={"billing_email": "bursar@school.example", "po_number": "PO-9"},
        headers=admin_of_test_school_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "invoice_open"
    assert resp.json()["method"] == "invoice"

    # A brand-new session over the same DB: the row only exists if the endpoint
    # committed (get_async_session does not commit on teardown).
    session.expire_all()
    grant = subscription_repository.get_by_id(session, invoice_pending_grant_id(wid))
    assert grant is not None
    assert grant.is_active is True
    persisted = school_repository.get_by_wriveted_id(session, str(wid))
    assert persisted.state == SchoolState.ACTIVE


@pytest.mark.asyncio
async def test_billing_endpoints_reject_lms_service_account(
    async_client,
    test_school,
    lms_service_account_token_for_school,
    monkeypatch,
):
    """A global (unscoped) LMS service-account token must NOT reach another
    school's billing endpoints — billing is gated on the dedicated ``billing``
    action, not the broad ``update`` that role:lms holds."""
    _configure_billing_settings(monkeypatch)
    wid = test_school.wriveted_identifier
    headers = {"Authorization": f"bearer {lms_service_account_token_for_school}"}

    portal = await async_client.post(
        f"/v1/school/{wid}/billing-portal", headers=headers
    )
    assert portal.status_code == 403, portal.text

    invoice = await async_client.post(
        f"/v1/school/{wid}/invoice-subscription",
        json={"billing_email": "bursar@school.example"},
        headers=headers,
    )
    assert invoice.status_code == 403, invoice.text


@pytest.mark.asyncio
async def test_billing_portal_allows_schooladmin_and_superuser(
    async_client,
    test_school,
    admin_of_test_school_headers,
    backend_service_account_headers,
    monkeypatch,
):
    """Legitimate billing callers keep access: the school's own admin and a
    superuser (backend/admin) pass the ``billing`` gate. No real subscription
    exists, so the endpoint returns 404 (not 403) — proving the gate passed."""
    _configure_billing_settings(monkeypatch)
    wid = test_school.wriveted_identifier

    for headers in (admin_of_test_school_headers, backend_service_account_headers):
        resp = await async_client.post(
            f"/v1/school/{wid}/billing-portal", headers=headers
        )
        assert resp.status_code != 403, resp.text
        assert resp.status_code == 404, resp.text


# --------------------------------------------------------------------------- #
# comp → paid conversion (findings #3): a comped school must not be blocked
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@patch("app.services.school_billing.stripe")
async def test_comp_school_can_convert_to_paid_invoice(
    mock_stripe, async_session, monkeypatch
):
    """A staff/invite/contribution comp must NOT block a new billing attempt — the
    school converts to paid. Only a real Stripe obligation or an invoice_pending /
    checkout_pending row blocks."""
    _configure_billing_settings(monkeypatch)
    school = await _new_school(async_session)
    await async_session.merge(Product(id=STAFF_COMP_PRODUCT_ID, name="Staff comp"))
    await async_session.flush()
    async_session.add(
        Subscription(
            id=staff_comp_id(school.wriveted_identifier),
            school_id=school.wriveted_identifier,
            type=SubscriptionType.SCHOOL,
            stripe_customer_id="",
            is_active=True,
            expiration=datetime.utcnow() + timedelta(days=90),
            product_id=STAFF_COMP_PRODUCT_ID,
            info={"source": STAFF_COMP_GRANT_SOURCE},
        )
    )
    await async_session.flush()

    mock_stripe.Customer.create.return_value = Mock(id=f"cus_{uuid4().hex}")
    sub_obj = MagicMock()
    sub_obj.id = "sub_new"
    sub_obj.get.return_value = {"hosted_invoice_url": "https://pay.stripe.test/i/x"}
    mock_stripe.Subscription.create.return_value = sub_obj

    result = await create_school_invoice_subscription(
        async_session, school, billing_email="bursar@school.example"
    )

    assert result.status == SchoolBillingAttemptStatus.INVOICE_OPEN
    mock_stripe.Subscription.create.assert_called_once()


# --------------------------------------------------------------------------- #
# card checkout reservation: no cross-path / repeat double-bill
# --------------------------------------------------------------------------- #


def _configure_checkout_settings(monkeypatch):
    _configure_billing_settings(monkeypatch)
    monkeypatch.setattr(
        school_billing_module.settings,
        "HUEY_BOOKS_APP_URL",
        "https://app.hueybooks.test",
    )


# --------------------------------------------------------------------------- #
# terminal non-payment: don't depend on Stripe cancel-overdue
# --------------------------------------------------------------------------- #


def test_deactivate_school_on_non_payment_drops_unpaid_school(session, test_school):
    """A voided/uncollectible invoice on a school whose access rests on an unpaid
    invoice retires the invoice_pending grant and drops the school — without
    waiting on Stripe's cancel-when-overdue setting."""
    _seed_invoice_school(session, test_school, with_staff_comp=False)
    test_school.state = SchoolState.ACTIVE
    session.add(test_school)
    session.commit()
    wid = test_school.wriveted_identifier

    dropped = deactivate_school_on_non_payment_sync(session, test_school)
    session.commit()

    assert dropped is True
    grant = subscription_repository.get_by_id(session, invoice_pending_grant_id(wid))
    assert grant.is_active is False
    refreshed = session.execute(
        select(School).where(School.wriveted_identifier == wid)
    ).scalar_one()
    assert refreshed.state == SchoolState.INACTIVE


def test_deactivate_school_on_non_payment_leaves_paying_school(session, test_school):
    """A voided invoice retires its pending grant but preserves paid access."""
    _seed_invoice_school(session, test_school, with_staff_comp=False)
    test_school.state = SchoolState.ACTIVE
    session.add(test_school)
    session.merge(Product(id=INVOICE_PRICE_ID, name="School (invoice)"))
    wid = test_school.wriveted_identifier
    session.add(
        Subscription(
            id="sub_paying",
            school_id=wid,
            type=SubscriptionType.SCHOOL,
            stripe_customer_id="cus_paying",
            is_active=True,
            expiration=datetime.utcnow() + timedelta(days=300),
            product_id=INVOICE_PRICE_ID,
            paid_at=datetime.utcnow(),
            stripe_status="active",
        )
    )
    session.commit()

    dropped = deactivate_school_on_non_payment_sync(session, test_school)
    session.commit()

    assert dropped is False
    grant = subscription_repository.get_by_id(session, invoice_pending_grant_id(wid))
    assert grant.is_active is False
    refreshed = session.execute(
        select(School).where(School.wriveted_identifier == wid)
    ).scalar_one()
    assert refreshed.state == SchoolState.ACTIVE

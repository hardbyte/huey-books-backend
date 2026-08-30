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
from app.models.subscription import Subscription, SubscriptionType
from app.repositories.school_repository import school_repository
from app.repositories.subscription_repository import subscription_repository
from app.services.school_access import (
    INVOICE_PENDING_GRANT_SOURCE,
    INVOICE_PENDING_PRODUCT_ID,
    STAFF_COMP_GRANT_SOURCE,
    STAFF_COMP_PRODUCT_ID,
    invoice_pending_grant_id,
    staff_comp_id,
)
from app.services.school_billing import (
    SchoolInvoiceConflictError,
    create_school_billing_portal_session,
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

    monkeypatch.setattr(school_billing.settings, "STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setattr(
        school_billing.settings, "STRIPE_SCHOOL_PRICE_IDS", [INVOICE_PRICE_ID]
    )
    monkeypatch.setattr(
        school_billing.settings, "STRIPE_SCHOOL_PRICE_IDS_BY_COUNTRY", {}
    )
    monkeypatch.setattr(school_billing.settings, "INVOICE_DAYS_UNTIL_DUE", 30)
    monkeypatch.setattr(school_billing.settings, "INVOICE_PENDING_GRACE_DAYS", 14)


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

    mock_stripe.Customer.create.return_value = Mock(id="cus_new")
    sub_obj = MagicMock()
    sub_obj.id = "sub_new"
    sub_obj.get.return_value = {"hosted_invoice_url": "https://pay.stripe.test/i/x"}
    mock_stripe.Subscription.create.return_value = sub_obj

    before = datetime.utcnow()
    result = await create_school_invoice_subscription(
        async_session,
        school,
        billing_email="bursar@school.example",
        billing_name="School Bursar",
        po_number="PO-12345",
    )

    assert result["status"] == "invoice_sent"
    assert result["hosted_invoice_url"] == "https://pay.stripe.test/i/x"

    # Customer created with school metadata + PO custom field (no existing one).
    mock_stripe.Customer.create.assert_called_once()
    _, cust_kwargs = mock_stripe.Customer.create.call_args
    assert cust_kwargs["metadata"] == {
        "wriveted_school_id": str(school.wriveted_identifier)
    }
    assert cust_kwargs["invoice_settings"]["custom_fields"] == [
        {"name": "PO number", "value": "PO-12345"}
    ]

    # Subscription created as a send_invoice net-terms sub.
    _, sub_kwargs = mock_stripe.Subscription.create.call_args
    assert sub_kwargs["customer"] == "cus_new"
    assert sub_kwargs["collection_method"] == "send_invoice"
    assert sub_kwargs["days_until_due"] == 30
    assert sub_kwargs["items"] == [{"price": INVOICE_PRICE_ID}]
    assert sub_kwargs["metadata"] == {
        "wriveted_school_id": str(school.wriveted_identifier)
    }

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
async def test_create_invoice_subscription_reuses_existing_customer(
    mock_stripe, async_session, monkeypatch
):
    _configure_billing_settings(monkeypatch)
    school = await _new_school(async_session)
    # A prior (now inactive) subscription carries the known Stripe customer id.
    await _price_product(async_session)
    async_session.add(
        Subscription(
            id="sub_old",
            school_id=school.wriveted_identifier,
            type=SubscriptionType.SCHOOL,
            stripe_customer_id="cus_existing",
            is_active=False,
            expiration=datetime.utcnow() - timedelta(days=5),
            product_id=INVOICE_PRICE_ID,
        )
    )
    await async_session.flush()

    mock_stripe.Customer.modify.return_value = Mock(id="cus_existing")
    sub_obj = MagicMock()
    sub_obj.id = "sub_new"
    sub_obj.get.return_value = None  # no latest_invoice expanded
    mock_stripe.Subscription.create.return_value = sub_obj

    result = await create_school_invoice_subscription(
        async_session, school, billing_email="bursar@school.example"
    )

    # Existing customer reused (modify), never a duplicate create.
    mock_stripe.Customer.modify.assert_called_once()
    assert mock_stripe.Customer.modify.call_args[0][0] == "cus_existing"
    mock_stripe.Customer.create.assert_not_called()
    assert result["hosted_invoice_url"] is None


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

    with pytest.raises(SchoolInvoiceConflictError):
        await create_school_invoice_subscription(
            async_session, school, billing_email="bursar@school.example"
        )
    mock_stripe.Subscription.create.assert_not_called()


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
            expiration=datetime.utcnow() + timedelta(days=30),
            product_id=INVOICE_PRICE_ID,
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

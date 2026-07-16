"""Integration tests for the "contribute a month" flow via Stripe webhooks.

Covers the crediting model:
- a contribution to a school with an active *Stripe* subscription becomes a
  customer-balance credit (currency-validated, fail-soft);
- a contribution to a school with no such subscription buys a bounded comped
  grant (creating or extending a Subscription and activating the school);
- the lapse sweep deactivates schools whose grant has expired, but not schools
  kept alive by a live Stripe subscription;
- duplicate webhook deliveries are idempotent.
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, Mock, patch
from uuid import uuid4

import pytest
import stripe
from sqlalchemy import select, text

from app.models.product import Product
from app.models.school import School, SchoolState
from app.models.stripe_contribution import StripeContributionReceipt
from app.models.subscription import Subscription, SubscriptionType
from app.repositories.school_repository import school_repository
from app.repositories.subscription_repository import subscription_repository
from app.services.stripe_events import (
    CONTRIBUTION_GRANT_PRODUCT_ID,
    CONTRIBUTION_GRANT_SOURCE,
    CONTRIBUTION_GRANT_SUBSCRIPTION_PREFIX,
    _handle_checkout_session_completed,
    _handle_contribution_checkout_completed,
    _handle_subscription_cancelled,
    _is_contribution_checkout,
)


def _grant_id(school) -> str:
    return f"{CONTRIBUTION_GRANT_SUBSCRIPTION_PREFIX}{school.wriveted_identifier}"


def _email_count(session) -> int:
    session.rollback()
    return session.execute(
        text("SELECT COUNT(*) FROM event_outbox WHERE event_type='email_notification'")
    ).scalar()


def _contribution_event(test_school, session_id="cs_contrib", amount_total=5000) -> dict:
    return {
        "id": session_id,
        "object": "checkout.session",
        "mode": "payment",
        "metadata": {
            "kind": "school_contribution",
            "wriveted_school_id": str(test_school.wriveted_identifier),
        },
        "client_reference_id": str(test_school.wriveted_identifier),
        "payment_status": "paid",
        "amount_total": amount_total,
        "currency": "aud",
        "customer_details": {"email": "payer@example.com"},
    }


def _fresh_school(session, test_school):
    """Re-fetch the school so its subscription relationship is loaded."""
    return school_repository.get_by_wriveted_id(
        session, wriveted_id=str(test_school.wriveted_identifier)
    )


def _give_active_stripe_subscription(session, test_school):
    # merge (not add) so the shared Product row survives across tests without a
    # primary-key collision (only the school is cleaned up between tests).
    session.merge(Product(id="price_contrib_active", name="Supporter School"))
    session.flush()
    session.add(
        Subscription(
            id="sub_contrib_active",
            product_id="price_contrib_active",
            stripe_customer_id="cus_contrib_active",
            school_id=test_school.wriveted_identifier,
            type=SubscriptionType.SCHOOL,
            is_active=True,
            expiration=datetime(2099, 1, 1),
        )
    )
    session.commit()


def _make_pending(session, test_school):
    test_school.state = SchoolState.PENDING
    test_school.info = {
        **(test_school.info or {}),
        "onboarding": {
            "contact_email": "contact@school.example",
            "contact_name": "Sam",
        },
    }
    session.add(test_school)
    session.commit()


# --- routing (M1: strictly by metadata kind, never bare mode) ---


def test_routing_matches_only_on_metadata_kind():
    assert _is_contribution_checkout({"mode": "payment", "metadata": {}}) is False
    assert _is_contribution_checkout({"mode": "payment"}) is False
    assert (
        _is_contribution_checkout(
            {"mode": "subscription", "metadata": {"kind": "school_contribution"}}
        )
        is True
    )
    assert _is_contribution_checkout({"metadata": {"kind": "other"}}) is False


# --- bounded grant (no active Stripe subscription) ---


@patch("app.services.stripe_events.StripeCustomer")
def test_contribution_without_subscription_creates_proportional_grant(
    mock_cust, session, test_school
):
    _make_pending(session, test_school)
    school = _fresh_school(session, test_school)

    # $50 at the default $25/mo notional rate -> ~60 days of access.
    before = datetime.utcnow()
    _handle_contribution_checkout_completed(
        session, school, _contribution_event(test_school, "cs_grant", amount_total=5000)
    )

    mock_cust.create_balance_transaction.assert_not_called()
    session.rollback()
    session.refresh(test_school)
    assert test_school.state == SchoolState.ACTIVE

    grant = subscription_repository.get_by_id(session, _grant_id(test_school))
    assert grant is not None
    assert grant.is_active
    assert (grant.info or {}).get("source") == CONTRIBUTION_GRANT_SOURCE
    assert grant.stripe_customer_id == ""
    granted_days = (grant.expiration - before).days
    assert 59 <= granted_days <= 61


@patch("app.services.stripe_events.StripeCustomer")
def test_larger_contribution_grants_more_days(mock_cust, session, test_school):
    _make_pending(session, test_school)
    before = datetime.utcnow()

    # $150 -> ~180 days, materially more than the $50 -> ~60 days case above.
    _handle_contribution_checkout_completed(
        session,
        _fresh_school(session, test_school),
        _contribution_event(test_school, "cs_big", amount_total=15000),
    )
    session.rollback()
    grant = subscription_repository.get_by_id(session, _grant_id(test_school))
    granted_days = (grant.expiration - before).days
    assert 179 <= granted_days <= 181


@patch("app.services.stripe_events.StripeCustomer")
def test_repeat_contribution_extends_by_proportional_days(
    mock_cust, session, test_school
):
    _make_pending(session, test_school)

    _handle_contribution_checkout_completed(
        session,
        _fresh_school(session, test_school),
        _contribution_event(test_school, "cs_g1", amount_total=5000),
    )
    session.rollback()
    first_expiry = subscription_repository.get_by_id(
        session, _grant_id(test_school)
    ).expiration

    # A second $50 contribution extends by ~60 more days from the first expiry.
    _handle_contribution_checkout_completed(
        session,
        _fresh_school(session, test_school),
        _contribution_event(test_school, "cs_g2", amount_total=5000),
    )
    session.rollback()
    second_expiry = subscription_repository.get_by_id(
        session, _grant_id(test_school)
    ).expiration

    extra_days = (second_expiry - first_expiry).days
    assert 59 <= extra_days <= 61


# --- active Stripe subscription -> customer balance credit ---


@patch("app.services.stripe_events.StripeCustomer")
def test_contribution_with_active_stripe_subscription_credits_balance(
    mock_cust, session, test_school
):
    _give_active_stripe_subscription(session, test_school)
    mock_cust.retrieve.return_value = {"currency": "aud"}
    school = _fresh_school(session, test_school)

    _handle_contribution_checkout_completed(
        session, school, _contribution_event(test_school, "cs_credit")
    )

    mock_cust.create_balance_transaction.assert_called_once()
    _, kwargs = mock_cust.create_balance_transaction.call_args
    assert kwargs["amount"] == -5000
    assert kwargs["currency"] == "aud"
    assert kwargs["idempotency_key"] == "contribution-cs_credit"

    session.rollback()
    session.refresh(test_school)
    assert test_school.state == SchoolState.ACTIVE  # unchanged
    receipt = session.get(StripeContributionReceipt, "cs_credit")
    assert receipt.crediting == "balance_credit"


@patch("app.services.stripe_events.StripeCustomer")
def test_contribution_currency_mismatch_soft_fails(mock_cust, session, test_school):
    _give_active_stripe_subscription(session, test_school)
    # Customer balance is in USD; the AUD contribution must not be force-credited.
    mock_cust.retrieve.return_value = {"currency": "usd"}
    school = _fresh_school(session, test_school)

    # Must not raise (a raise would poison the webhook into infinite retries).
    _handle_contribution_checkout_completed(
        session, school, _contribution_event(test_school, "cs_mismatch")
    )

    mock_cust.create_balance_transaction.assert_not_called()
    session.rollback()
    receipt = session.get(StripeContributionReceipt, "cs_mismatch")
    assert receipt.crediting == "credit_failed"


@patch("app.services.stripe_events.StripeCustomer")
def test_contribution_permanent_stripe_error_soft_fails(
    mock_cust, session, test_school
):
    _give_active_stripe_subscription(session, test_school)
    mock_cust.retrieve.return_value = {"currency": "aud"}
    # InvalidRequestError is a permanent client error: retrying won't help.
    mock_cust.create_balance_transaction.side_effect = stripe.error.InvalidRequestError(
        "bad param", "amount"
    )
    school = _fresh_school(session, test_school)

    # Must not raise; recorded as credit_failed for manual handling.
    _handle_contribution_checkout_completed(
        session, school, _contribution_event(test_school, "cs_perm")
    )

    session.rollback()
    receipt = session.get(StripeContributionReceipt, "cs_perm")
    assert receipt.crediting == "credit_failed"


@patch("app.services.stripe_events.StripeCustomer")
def test_contribution_transient_stripe_error_reraises(mock_cust, session, test_school):
    _give_active_stripe_subscription(session, test_school)
    mock_cust.retrieve.return_value = {"currency": "aud"}
    # A connection error is potentially transient: re-raise so the task retries.
    mock_cust.create_balance_transaction.side_effect = stripe.error.APIConnectionError(
        "network blip"
    )
    school = _fresh_school(session, test_school)

    with pytest.raises(stripe.error.APIConnectionError):
        _handle_contribution_checkout_completed(
            session, school, _contribution_event(test_school, "cs_transient")
        )

    # The idempotency claim was rolled back with the aborted transaction, so a
    # retry can reprocess.
    session.rollback()
    assert session.get(StripeContributionReceipt, "cs_transient") is None


# --- idempotency ---


@patch("app.services.stripe_events.StripeCustomer")
def test_duplicate_contribution_event_is_noop(mock_cust, session, test_school):
    _make_pending(session, test_school)

    _handle_contribution_checkout_completed(
        session,
        _fresh_school(session, test_school),
        _contribution_event(test_school, "cs_dup"),
    )
    emails_after_first = _email_count(session)
    first_expiry = subscription_repository.get_by_id(
        session, _grant_id(test_school)
    ).expiration

    # Stripe redelivers the same event; already claimed -> full no-op.
    _handle_contribution_checkout_completed(
        session,
        _fresh_school(session, test_school),
        _contribution_event(test_school, "cs_dup"),
    )
    assert _email_count(session) == emails_after_first
    # Not double-extended.
    assert (
        subscription_repository.get_by_id(session, _grant_id(test_school)).expiration
        == first_expiry
    )


# --- grant -> Stripe subscription conversion ---


def _mock_checkout_stripe(mock_sub, mock_cust, mock_price, mock_prod):
    sub = MagicMock()
    sub.customer = "cus_convert"
    sub.current_period_end = 1893456000
    sub.__getitem__.return_value = {"data": [{"price": {"id": "price_convert"}}]}
    mock_sub.retrieve.return_value = sub

    cust = Mock(spec=["get", "metadata", "name", "save"])
    cust.get.return_value = "payer@example.com"
    cust.metadata = {}
    cust.name = "Payer"
    cust.save.return_value = None
    mock_cust.retrieve.return_value = cust

    mock_price.retrieve.return_value = Mock(product="prod_convert")
    product = Mock()
    product.name = "Supporter School"
    mock_prod.retrieve.return_value = product


@patch("app.services.stripe_events.StripeProduct")
@patch("app.services.stripe_events.StripePrice")
@patch("app.services.stripe_events.StripeCustomer")
@patch("app.services.stripe_events.StripeSubscription")
def test_grant_to_stripe_subscription_conversion_retires_grant(
    mock_sub, mock_cust, mock_price, mock_prod, session, test_school
):
    # 1. School gains a comped grant via a contribution (no Stripe calls here).
    _make_pending(session, test_school)
    _handle_contribution_checkout_completed(
        session,
        _fresh_school(session, test_school),
        _contribution_event(test_school, "cs_pre", amount_total=5000),
    )
    session.rollback()
    assert subscription_repository.get_by_id(session, _grant_id(test_school)).is_active

    # 2. The school then converts to a paying Stripe subscription.
    _mock_checkout_stripe(mock_sub, mock_cust, mock_price, mock_prod)
    _handle_checkout_session_completed(
        session,
        None,
        _fresh_school(session, test_school),
        {
            "id": "cs_convert",
            "object": "checkout.session",
            "subscription": "sub_convert",
            "client_reference_id": str(test_school.wriveted_identifier),
            "payment_status": "paid",
        },
    )
    session.rollback()
    session.expire_all()

    # The grant row survives (not orphan-deleted) but is retired.
    grant = subscription_repository.get_by_id(session, _grant_id(test_school))
    assert grant is not None
    assert grant.is_active is False

    # Exactly one active subscription row remains — the paying Stripe one.
    active = (
        session.execute(
            select(Subscription).where(
                Subscription.school_id == test_school.wriveted_identifier,
                Subscription.is_active.is_(True),
            )
        )
        .scalars()
        .all()
    )
    assert len(active) == 1
    assert active[0].stripe_customer_id != ""

    # School.subscription (uselist=False) resolves to the live paying one.
    school = _fresh_school(session, test_school)
    assert school.subscription is not None
    assert school.subscription.is_active
    assert school.subscription.stripe_customer_id != ""

    # The grant row is still queryable after a flush (delete-orphan didn't fire).
    session.commit()
    assert subscription_repository.get_by_id(session, _grant_id(test_school)) is not None


# --- cancellation with a live comp grant ---


def test_subscription_cancelled_keeps_school_active_with_live_grant(
    session, test_school
):
    test_school.state = SchoolState.ACTIVE
    session.add(test_school)
    session.merge(Product(id="price_cancel_grant", name="Real"))
    session.merge(Product(id=CONTRIBUTION_GRANT_PRODUCT_ID, name="comp"))
    session.flush()
    session.add(
        Subscription(
            id="sub_cancel_grant",
            product_id="price_cancel_grant",
            stripe_customer_id="cus_cg",
            school_id=test_school.wriveted_identifier,
            type=SubscriptionType.SCHOOL,
            is_active=True,
            expiration=datetime(2099, 1, 1),
        )
    )
    session.add(
        Subscription(
            id=_grant_id(test_school),
            product_id=CONTRIBUTION_GRANT_PRODUCT_ID,
            stripe_customer_id="",
            school_id=test_school.wriveted_identifier,
            type=SubscriptionType.SCHOOL,
            is_active=True,
            expiration=datetime.utcnow() + timedelta(days=30),
            info={"source": CONTRIBUTION_GRANT_SOURCE},
        )
    )
    session.commit()

    _handle_subscription_cancelled(
        session,
        None,
        None,
        {"id": "sub_cancel_grant", "object": "subscription", "ended_at": 1893456000},
    )

    session.rollback()
    session.refresh(test_school)
    # An unexpired comp grant keeps the school active despite the Stripe sub ending.
    assert test_school.state == SchoolState.ACTIVE


# --- has_active_subscription staff filter ---


def test_has_active_subscription_filter_excludes_comps_and_dedupes(
    session, test_school
):
    # test_school: a paying Stripe subscription AND a comp grant (two rows).
    _give_active_stripe_subscription(session, test_school)
    session.merge(Product(id=CONTRIBUTION_GRANT_PRODUCT_ID, name="comp"))
    session.flush()
    session.add(
        Subscription(
            id=_grant_id(test_school),
            product_id=CONTRIBUTION_GRANT_PRODUCT_ID,
            stripe_customer_id="",
            school_id=test_school.wriveted_identifier,
            type=SubscriptionType.SCHOOL,
            is_active=True,
            expiration=datetime.utcnow() + timedelta(days=30),
            info={"source": CONTRIBUTION_GRANT_SOURCE},
        )
    )
    # comp_only school: only a comp grant -> not "paying".
    comp_only = School(
        name="Comp Only", wriveted_identifier=uuid4(), state=SchoolState.ACTIVE
    )
    session.add(comp_only)
    session.flush()
    session.add(
        Subscription(
            id=_grant_id(comp_only),
            product_id=CONTRIBUTION_GRANT_PRODUCT_ID,
            stripe_customer_id="",
            school_id=comp_only.wriveted_identifier,
            type=SubscriptionType.SCHOOL,
            is_active=True,
            expiration=datetime.utcnow() + timedelta(days=30),
            info={"source": CONTRIBUTION_GRANT_SOURCE},
        )
    )
    session.commit()

    rows = (
        session.execute(
            school_repository.get_all_query_with_optional_filters(
                session, has_active_subscription=True
            )
        )
        .scalars()
        .all()
    )
    ids = [s.wriveted_identifier for s in rows]
    # The paying school appears exactly once (no cartesian dupes from two rows).
    assert ids.count(test_school.wriveted_identifier) == 1
    # A comp-only school is not counted as "paying".
    assert comp_only.wriveted_identifier not in ids


# --- lapse sweep ---


@pytest.mark.asyncio
async def test_lapse_deactivates_expired_grant_not_stripe_schools(async_session):
    from app.api.internal import handle_lapse_expired_schools

    now = datetime.utcnow()
    await async_session.merge(Product(id=CONTRIBUTION_GRANT_PRODUCT_ID, name="comp"))
    real_price_id = f"price_real_{uuid4().hex[:8]}"
    await async_session.merge(Product(id=real_price_id, name="Real"))
    await async_session.flush()

    # School A: expired grant, no Stripe subscription -> should lapse.
    school_a = School(
        name="Grant School", wriveted_identifier=uuid4(), state=SchoolState.ACTIVE
    )
    # School B: expired grant BUT a live Stripe subscription -> must stay ACTIVE.
    school_b = School(
        name="Stripe School", wriveted_identifier=uuid4(), state=SchoolState.ACTIVE
    )
    async_session.add_all([school_a, school_b])
    await async_session.flush()

    async_session.add_all(
        [
            Subscription(
                id=f"{CONTRIBUTION_GRANT_SUBSCRIPTION_PREFIX}{school_a.wriveted_identifier}",
                school_id=school_a.wriveted_identifier,
                type=SubscriptionType.SCHOOL,
                stripe_customer_id="",
                is_active=True,
                expiration=now - timedelta(days=1),
                product_id=CONTRIBUTION_GRANT_PRODUCT_ID,
                info={"source": CONTRIBUTION_GRANT_SOURCE},
            ),
            Subscription(
                id=f"{CONTRIBUTION_GRANT_SUBSCRIPTION_PREFIX}{school_b.wriveted_identifier}",
                school_id=school_b.wriveted_identifier,
                type=SubscriptionType.SCHOOL,
                stripe_customer_id="",
                is_active=True,
                expiration=now - timedelta(days=1),
                product_id=CONTRIBUTION_GRANT_PRODUCT_ID,
                info={"source": CONTRIBUTION_GRANT_SOURCE},
            ),
            Subscription(
                id=f"sub_stripe_{uuid4().hex[:8]}",
                school_id=school_b.wriveted_identifier,
                type=SubscriptionType.SCHOOL,
                stripe_customer_id="cus_real",
                is_active=True,
                expiration=now + timedelta(days=30),
                product_id=real_price_id,
            ),
        ]
    )
    await async_session.commit()

    result = await handle_lapse_expired_schools(async_session)

    await async_session.refresh(school_a)
    await async_session.refresh(school_b)
    assert school_a.state == SchoolState.INACTIVE
    assert school_b.state == SchoolState.ACTIVE
    assert result["lapsed"] == 1

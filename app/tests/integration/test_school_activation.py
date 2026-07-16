"""Integration tests for school activation/deactivation via Stripe webhooks.

Covers the money-gates-access rules: only a paid checkout activates a school,
unpaid/duplicate events don't, and cancellation deactivates.
"""

from datetime import datetime
from unittest.mock import MagicMock, Mock, patch

from sqlalchemy import text

from app.models.product import Product
from app.models.school import SchoolState
from app.models.subscription import Subscription, SubscriptionType
from app.services.stripe_events import (
    _handle_checkout_session_completed,
    _handle_subscription_cancelled,
)

PRICE_ID = "price_school_test"


def _mock_stripe(mock_sub, mock_cust, mock_price, mock_prod, *, customer_email):
    sub = MagicMock()
    sub.customer = "cus_test"
    sub.current_period_end = 1893456000
    sub.__getitem__.return_value = {"data": [{"price": {"id": PRICE_ID}}]}
    mock_sub.retrieve.return_value = sub

    cust = Mock(spec=["get", "metadata", "name", "save"])
    cust.get.return_value = customer_email
    cust.metadata = {}
    cust.name = "Payer"
    cust.save.return_value = None
    mock_cust.retrieve.return_value = cust

    mock_price.retrieve.return_value = Mock(product="prod_test")
    product = Mock()
    product.name = "Supporter School"
    mock_prod.retrieve.return_value = product


def _make_pending_school(session, test_school):
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


def _email_count(session) -> int:
    session.rollback()
    return session.execute(
        text("SELECT COUNT(*) FROM event_outbox WHERE event_type='email_notification'")
    ).scalar()


def _checkout_event(test_school, payment_status: str, session_id="cs_test") -> dict:
    return {
        "id": session_id,
        "object": "checkout.session",
        "subscription": f"sub_{session_id}",
        "client_reference_id": str(test_school.wriveted_identifier),
        "payment_status": payment_status,
    }


@patch("app.services.stripe_events.StripeProduct")
@patch("app.services.stripe_events.StripePrice")
@patch("app.services.stripe_events.StripeCustomer")
@patch("app.services.stripe_events.StripeSubscription")
def test_paid_checkout_activates_school_and_emails(
    mock_sub, mock_cust, mock_price, mock_prod, session, test_school
):
    _mock_stripe(
        mock_sub, mock_cust, mock_price, mock_prod, customer_email="c@s.example"
    )
    _make_pending_school(session, test_school)
    before = _email_count(session)

    _handle_checkout_session_completed(
        session, None, test_school, _checkout_event(test_school, "paid")
    )

    session.rollback()
    session.refresh(test_school)
    assert test_school.state == SchoolState.ACTIVE
    assert _email_count(session) == before + 1  # one receipt


@patch("app.services.stripe_events.StripeProduct")
@patch("app.services.stripe_events.StripePrice")
@patch("app.services.stripe_events.StripeCustomer")
@patch("app.services.stripe_events.StripeSubscription")
def test_unpaid_checkout_does_not_activate(
    mock_sub, mock_cust, mock_price, mock_prod, session, test_school
):
    _mock_stripe(
        mock_sub, mock_cust, mock_price, mock_prod, customer_email="c@s.example"
    )
    _make_pending_school(session, test_school)

    _handle_checkout_session_completed(
        session, None, test_school, _checkout_event(test_school, "unpaid")
    )

    session.rollback()
    session.refresh(test_school)
    assert test_school.state == SchoolState.PENDING  # not activated


@patch("app.services.stripe_events.StripeProduct")
@patch("app.services.stripe_events.StripePrice")
@patch("app.services.stripe_events.StripeCustomer")
@patch("app.services.stripe_events.StripeSubscription")
def test_duplicate_paid_event_does_not_re_email(
    mock_sub, mock_cust, mock_price, mock_prod, session, test_school
):
    _mock_stripe(
        mock_sub, mock_cust, mock_price, mock_prod, customer_email="c@s.example"
    )
    _make_pending_school(session, test_school)

    _handle_checkout_session_completed(
        session, None, test_school, _checkout_event(test_school, "paid", "cs_1")
    )
    after_first = _email_count(session)
    # Stripe redelivers the same event; school already active -> no second receipt.
    _handle_checkout_session_completed(
        session, None, test_school, _checkout_event(test_school, "paid", "cs_2")
    )
    assert _email_count(session) == after_first


def test_subscription_cancelled_deactivates_school(session, test_school):
    test_school.state = SchoolState.ACTIVE
    session.add(test_school)
    session.add(Product(id="price_cancel_test", name="Supporter School"))
    session.flush()
    session.add(
        Subscription(
            id="sub_cancel",
            product_id="price_cancel_test",
            stripe_customer_id="cus_cancel",
            school_id=test_school.wriveted_identifier,
            type=SubscriptionType.SCHOOL,
            is_active=True,
            expiration=datetime(2099, 1, 1),
        )
    )
    session.commit()

    _handle_subscription_cancelled(
        session,
        None,
        None,
        {"id": "sub_cancel", "object": "subscription", "ended_at": 1893456000},
    )

    session.rollback()
    session.refresh(test_school)
    assert test_school.state == SchoolState.INACTIVE

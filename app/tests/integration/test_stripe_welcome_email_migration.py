"""
Integration test for Stripe welcome email migration to EventOutbox.

Tests that subscription welcome emails are properly sent via EventOutbox
instead of direct background task queuing.
"""

from unittest.mock import MagicMock, Mock, patch
from uuid import uuid4

from sqlalchemy import text

from app.models.event_outbox import EventPriority
from app.models.parent import Parent
from app.models.user import User, UserAccountType
from app.services.stripe_events import _handle_checkout_session_completed


# @pytest.mark.asyncio  # Not needed for sync tests
def test_stripe_welcome_email_creates_outbox_event(session):
    """Test that Stripe subscription welcome emails create EventOutbox events."""

    # Create a parent user (required for welcome emails)
    # Note: Parent uses joined-table inheritance, so we need to create the Parent record specifically
    user_uuid = uuid4()
    parent_user = Parent(
        id=user_uuid,
        email=f"parent-{user_uuid}@test.com",
        name="Test Parent",
        is_active=True,
    )
    session.add(parent_user)
    session.commit()

    # Mock Stripe customer and subscription data
    checkout_session_data = {
        "id": "cs_test_123",
        "subscription": "sub_test_123",
        "customer": "cus_test_123",
        "client_reference_id": str(parent_user.id),
    }

    # Mock Stripe API calls
    with (
        patch("app.services.stripe_events.StripeSubscription") as mock_subscription,
        patch("app.services.stripe_events.StripeCustomer") as mock_customer,
        patch("app.services.stripe_events.StripePrice") as mock_price,
        patch("app.services.stripe_events.StripeProduct") as mock_product,
    ):
        # Set up mock responses that support both dict-like and attribute access
        subscription_mock = MagicMock()
        subscription_mock.customer = "cus_test_123"
        subscription_mock.current_period_end = 1672531200
        subscription_mock.__getitem__.return_value = {
            "data": [{"price": {"id": "price_test_123"}}]
        }
        mock_subscription.retrieve.return_value = subscription_mock

        customer_mock = Mock(spec=["get", "metadata", "name", "save"])
        customer_mock.get.return_value = f"parent-{user_uuid}@test.com"
        customer_mock.metadata = {}
        customer_mock.name = "Test Parent"  # Set as simple attribute
        customer_mock.save.return_value = None
        mock_customer.retrieve.return_value = customer_mock

        mock_price.retrieve.return_value = Mock(product="prod_test_123")
        mock_product.retrieve.return_value = Mock(name="Test Product")
        # Ensure the name property returns a string, not a Mock object
        mock_product.retrieve.return_value.name = "Test Product"

        # Count existing outbox events
        result = session.execute(text("SELECT COUNT(*) FROM event_outbox"))
        initial_count = result.scalar()

        # Call the Stripe handler
        result = _handle_checkout_session_completed(
            session, parent_user, None, checkout_session_data
        )

        # Roll back rather than commit: the outbox row must have been committed by
        # the handler itself. A manual commit here would mask a missing commit in
        # the production path (which is exactly how that bug shipped before).
        session.rollback()

        # Verify an outbox event was created
        result = session.execute(text("SELECT COUNT(*) FROM event_outbox"))
        final_count = result.scalar()

        assert final_count > initial_count, "No EventOutbox events were created"

        # Check the specific email event was created
        result = session.execute(
            text("""
                SELECT event_type, destination, priority, max_retries, payload
                FROM event_outbox 
                WHERE event_type = 'email_notification'
                ORDER BY created_at DESC 
                LIMIT 1
            """)
        )

        email_event = result.fetchone()
        assert email_event is not None, "No email_notification event found"

        # Verify event properties
        assert email_event.event_type == "email_notification"
        assert (
            email_event.destination == "sendgrid:transactional"
        )  # Email type appended to destination
        assert (
            email_event.priority.lower() == EventPriority.CRITICAL.value.lower()
        )  # TRANSACTIONAL emails are CRITICAL (case insensitive)
        assert email_event.max_retries == 5  # TRANSACTIONAL emails get 5 retries

        # Verify email payload contains subscription welcome email data
        import json

        # Handle payload as dict if already deserialized, otherwise parse JSON
        payload = (
            email_event.payload
            if isinstance(email_event.payload, dict)
            else json.loads(email_event.payload)
        )

        assert "email_data" in payload
        email_data = payload["email_data"]

        assert email_data["from_email"] == "orders@hueybooks.com"
        assert email_data["from_name"] == "Huey Books"
        assert email_data["to_emails"] == [f"parent-{user_uuid}@test.com"]
        assert email_data["subject"] == "Your Huey Books Membership"
        assert email_data["template_id"] == "d-fa829ecc76fc4e37ab4819abb6e0d188"

        # Verify template data
        template_data = email_data["template_data"]
        assert template_data["name"] == "Test Parent"
        assert template_data["checkout_session_id"] == "cs_test_123"

        # Verify metadata
        assert payload["email_type"] == "transactional"
        assert payload["user_id"] == str(parent_user.id)


# @pytest.mark.asyncio  # Not needed for sync tests
def test_stripe_welcome_email_not_sent_for_non_parent(session):
    """Test that welcome emails are not sent for non-parent users."""

    # Create a student user (should not get welcome emails)
    # Note: Student users are stored in the base User table, not a separate joined table
    user_uuid = uuid4()
    student_user = User(
        id=user_uuid,
        email=f"student-{user_uuid}@test.com",
        name="Test Student",
        type=UserAccountType.STUDENT,
        is_active=True,
    )
    session.add(student_user)
    session.commit()

    checkout_session_data = {
        "id": "cs_test_456",
        "subscription": "sub_test_456",
        "customer": "cus_test_456",
        "client_reference_id": str(student_user.id),
    }

    # Mock Stripe API calls
    with (
        patch("app.services.stripe_events.StripeSubscription") as mock_subscription,
        patch("app.services.stripe_events.StripeCustomer") as mock_customer,
        patch("app.services.stripe_events.StripePrice") as mock_price,
        patch("app.services.stripe_events.StripeProduct") as mock_product,
    ):
        subscription_mock = MagicMock()
        subscription_mock.customer = "cus_test_456"
        subscription_mock.current_period_end = 1672531200
        subscription_mock.__getitem__.return_value = {
            "data": [{"price": {"id": "price_test_456"}}]
        }
        mock_subscription.retrieve.return_value = subscription_mock

        customer_mock = Mock(spec=["get", "metadata"])
        customer_mock.get.return_value = f"student-{user_uuid}@test.com"
        customer_mock.metadata = {}
        mock_customer.retrieve.return_value = customer_mock

        mock_price.retrieve.return_value = Mock(product="prod_test_456")
        mock_product.retrieve.return_value = Mock(name="Test Product")
        # Ensure the name property returns a string
        mock_product.retrieve.return_value.name = "Test Product"

        # Count email events before
        result = session.execute(
            text(
                "SELECT COUNT(*) FROM event_outbox WHERE event_type = 'email_notification'"
            )
        )
        initial_email_count = result.scalar()

        # Call the handler
        _handle_checkout_session_completed(
            session, student_user, None, checkout_session_data
        )

        session.commit()

        # Verify no additional email events were created
        result = session.execute(
            text(
                "SELECT COUNT(*) FROM event_outbox WHERE event_type = 'email_notification'"
            )
        )
        final_email_count = result.scalar()

        assert final_email_count == initial_email_count, (
            "Welcome email was sent for non-parent user"
        )


# @pytest.mark.asyncio  # Not needed for sync tests
def test_stripe_welcome_email_not_sent_without_customer_email(session):
    """Test that welcome emails are not sent when customer has no email."""

    # Create a parent user
    user_uuid = uuid4()
    parent_user = Parent(
        id=user_uuid,
        email=f"parent-{user_uuid}@test.com",
        name="Test Parent No Email",
        is_active=True,
    )
    session.add(parent_user)
    session.commit()

    checkout_session_data = {
        "id": "cs_test_789",
        "subscription": "sub_test_789",
        "customer": "cus_test_789",
        "client_reference_id": str(parent_user.id),
    }

    # Mock Stripe API calls with customer that has no email
    with (
        patch("app.services.stripe_events.StripeSubscription") as mock_subscription,
        patch("app.services.stripe_events.StripeCustomer") as mock_customer,
        patch("app.services.stripe_events.StripePrice") as mock_price,
        patch("app.services.stripe_events.StripeProduct") as mock_product,
    ):
        subscription_mock = MagicMock()
        subscription_mock.customer = "cus_test_789"
        subscription_mock.current_period_end = 1672531200
        subscription_mock.__getitem__.return_value = {
            "data": [{"price": {"id": "price_test_789"}}]
        }
        mock_subscription.retrieve.return_value = subscription_mock

        # Customer with no email
        customer_mock = Mock(spec=["get", "metadata", "name"])
        customer_mock.get.return_value = None  # No email
        customer_mock.metadata = {}
        customer_mock.name = "Test Parent"
        mock_customer.retrieve.return_value = customer_mock

        mock_price.retrieve.return_value = Mock(product="prod_test_789")
        mock_product.retrieve.return_value = Mock(name="Test Product")
        # Ensure the name property returns a string
        mock_product.retrieve.return_value.name = "Test Product"

        # Count email events before
        result = session.execute(
            text(
                "SELECT COUNT(*) FROM event_outbox WHERE event_type = 'email_notification'"
            )
        )
        initial_email_count = result.scalar()

        # Call the handler
        _handle_checkout_session_completed(
            session, parent_user, None, checkout_session_data
        )

        session.commit()

        # Verify no email events were created (no customer email)
        result = session.execute(
            text(
                "SELECT COUNT(*) FROM event_outbox WHERE event_type = 'email_notification'"
            )
        )
        final_email_count = result.scalar()

        assert final_email_count == initial_email_count, (
            "Welcome email was sent without customer email"
        )

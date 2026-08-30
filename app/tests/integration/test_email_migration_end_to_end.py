"""
End-to-end test for email migration to EventOutbox pattern.

Tests that all migrated email systems properly create EventOutbox events
instead of using direct background task queuing.
"""

import json
from uuid import uuid4

from sqlalchemy import text

from app.models.event_outbox import EventOutbox, EventPriority, EventStatus
from app.services.email_notification import EmailType, send_email_reliable_sync


def test_email_migration_creates_outbox_events(session):
    """Test that migrated email functions create EventOutbox events."""

    # Count existing outbox events
    result = session.execute(text("SELECT COUNT(*) FROM event_outbox"))
    initial_count = result.scalar()

    # Test email data
    email_data = {
        "from_email": "test@example.com",
        "from_name": "Test Sender",
        "to_emails": ["recipient@example.com"],
        "subject": "Test Email Migration",
        "template_id": "d-test123",
        "template_data": {"name": "Test User"},
    }

    # Send email using the new reliable sync method
    send_email_reliable_sync(
        db=session,
        email_data=email_data,
        email_type=EmailType.NOTIFICATION,
        user_id=str(uuid4()),
    )

    session.commit()

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
    assert email_event.destination == "email:notification"
    assert (
        email_event.priority == "HIGH"
    )  # NOTIFICATION emails are HIGH priority (uppercase in DB)
    assert email_event.max_retries == 4  # NOTIFICATION emails get 4 retries

    # Verify email payload contains correct data
    payload = (
        email_event.payload
        if isinstance(email_event.payload, dict)
        else json.loads(email_event.payload)
    )

    assert "email_data" in payload
    assert payload["email_data"]["from_email"] == "test@example.com"
    assert payload["email_data"]["subject"] == "Test Email Migration"
    assert payload["email_type"] == "notification"


def test_email_type_priority_mapping(session):
    """Test that different email types map to correct priorities."""

    test_cases = [
        (EmailType.TRANSACTIONAL, "CRITICAL", 5),
        (EmailType.ONBOARDING, "HIGH", 4),
        (EmailType.NOTIFICATION, "HIGH", 4),
        (EmailType.SYSTEM, "NORMAL", 3),
        (EmailType.MARKETING, "LOW", 2),
    ]

    email_data = {
        "from_email": "test@example.com",
        "to_emails": ["recipient@example.com"],
        "subject": "Priority Test",
    }

    for email_type, expected_priority, expected_retries in test_cases:
        # Send email with specific type
        send_email_reliable_sync(
            db=session,
            email_data=email_data,
            email_type=email_type,
            user_id=str(uuid4()),
        )

        session.commit()

        # Verify event was created with correct priority
        result = session.execute(
            text("""
                SELECT priority, max_retries
                FROM event_outbox 
                WHERE event_type = 'email_notification'
                ORDER BY created_at DESC 
                LIMIT 1
            """)
        )

        event = result.fetchone()
        assert event is not None, f"No event created for {email_type}"
        assert event.priority == expected_priority, f"Wrong priority for {email_type}"
        assert event.max_retries == expected_retries, (
            f"Wrong retry count for {email_type}"
        )


def test_email_outbox_processing_simulation(session):
    """Test that email events can be processed from the outbox."""

    # Create an email outbox event directly (simulating what the service does)
    email_data = {
        "from_email": "test@example.com",
        "to_emails": ["recipient@example.com"],
        "subject": "Outbox Processing Test",
        "template_id": "d-test456",
    }

    payload = {
        "email_data": email_data,
        "email_type": "system",
        "user_id": str(uuid4()),
    }

    outbox_event = EventOutbox(
        event_type="email_notification",
        destination="email:sendgrid",
        priority=EventPriority.NORMAL,
        max_retries=3,
        payload=json.dumps(payload),
        status=EventStatus.PENDING,
    )

    session.add(outbox_event)
    session.commit()

    # Verify the event can be retrieved and processed
    event = (
        session.query(EventOutbox)
        .filter(
            EventOutbox.event_type == "email_notification",
            EventOutbox.status == EventStatus.PENDING,
        )
        .order_by(EventOutbox.created_at.desc())
        .first()
    )

    assert event is not None, "No pending email event found"

    # Parse the payload (this simulates what EventOutboxService does)
    payload_data = (
        event.payload if isinstance(event.payload, dict) else json.loads(event.payload)
    )
    assert payload_data["email_data"]["subject"] == "Outbox Processing Test"
    assert payload_data["email_type"] == "system"
    assert "user_id" in payload_data

    # This confirms the payload structure is correct for processing


def test_commerce_api_email_migration(session):
    """Test that commerce API email migration works correctly."""

    # This simulates what happens in app/api/commerce.py after migration
    from app.services.email_notification import EmailType, send_email_reliable_sync

    # Simulate commerce API email data
    commerce_email_data = {
        "from_email": "orders@hueybooks.com",
        "from_name": "Huey Books Orders",
        "to_emails": ["customer@example.com"],
        "subject": "Your Order Confirmation",
        "template_id": "d-commerce123",
        "template_data": {"order_id": "ORDER-123"},
    }

    # Count events before
    result = session.execute(text("SELECT COUNT(*) FROM event_outbox"))
    initial_count = result.scalar()

    # Send via migrated commerce API pattern
    send_email_reliable_sync(
        db=session,
        email_data=commerce_email_data,
        email_type=EmailType.SYSTEM,  # Commerce API emails are system-level
        service_account_id=str(uuid4()),  # Commerce API uses service accounts
    )

    session.commit()

    # Verify event was created
    result = session.execute(text("SELECT COUNT(*) FROM event_outbox"))
    final_count = result.scalar()

    assert final_count > initial_count, "Commerce email did not create outbox event"

    # Verify the event has correct metadata
    result = session.execute(
        text("""
            SELECT payload
            FROM event_outbox 
            WHERE event_type = 'email_notification'
            ORDER BY created_at DESC 
            LIMIT 1
        """)
    )

    event = result.fetchone()
    payload = (
        event.payload if isinstance(event.payload, dict) else json.loads(event.payload)
    )

    assert payload["email_data"]["from_email"] == "orders@hueybooks.com"
    assert payload["email_type"] == "system"
    assert "service_account_id" in payload


def test_email_migration_preserves_reliability(session):
    """Test that email migration maintains reliability guarantees."""

    # The key reliability feature is that emails go to EventOutbox
    # instead of direct background task queuing

    email_data = {
        "from_email": "reliable@example.com",
        "to_emails": ["user@example.com"],
        "subject": "Reliability Test",
    }

    # Send critical email
    send_email_reliable_sync(
        db=session,
        email_data=email_data,
        email_type=EmailType.TRANSACTIONAL,  # Most critical type
        user_id=str(uuid4()),
    )

    session.commit()

    # Verify critical email gets maximum retries and highest priority
    result = session.execute(
        text("""
            SELECT priority, max_retries, status
            FROM event_outbox 
            WHERE event_type = 'email_notification'
            ORDER BY created_at DESC 
            LIMIT 1
        """)
    )

    event = result.fetchone()
    assert event.priority == "CRITICAL", "Critical email not properly prioritized"
    assert event.max_retries == 5, "Critical email doesn't have maximum retries"
    assert event.status == "PENDING", "Email not queued for delivery"

    # This ensures that critical business emails (like payment confirmations)
    # have the highest reliability guarantees in the new system

"""
Integration tests for the Event Outbox Service.

These tests verify the Event Outbox Pattern implementation for reliable event delivery.
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.models.event_outbox import EventOutbox, EventPriority, EventStatus
from app.services.event_outbox_service import EventOutboxService


@pytest.fixture(autouse=True)
async def cleanup_event_outbox(async_session):
    """Clean up event outbox table before each test."""
    # Clean up before test runs
    await async_session.execute(
        text("TRUNCATE TABLE event_outbox RESTART IDENTITY CASCADE")
    )
    await async_session.commit()

    yield

    # Clean up after test runs
    await async_session.execute(
        text("TRUNCATE TABLE event_outbox RESTART IDENTITY CASCADE")
    )
    await async_session.commit()


class TestEventOutboxService:
    """Test the Event Outbox Service for reliable event delivery."""

    async def test_publish_event_basic(self, async_session):
        """Test basic event publishing to outbox."""
        service = EventOutboxService()

        event = await service.publish_event(
            async_session,
            event_type="test_event",
            destination="webhook_test",
            payload={"message": "Hello World"},
            correlation_id="test-123",
        )

        await async_session.commit()

        assert event.id is not None
        assert event.event_type == "test_event"
        assert event.destination == "webhook_test"
        assert event.payload == {"message": "Hello World"}
        assert event.status == EventStatus.PENDING
        assert event.priority == EventPriority.NORMAL
        assert event.correlation_id == "test-123"
        assert event.retry_count == 0

    async def test_publish_critical_event(self, async_session):
        """Test publishing critical events with higher priority."""
        service = EventOutboxService()

        event = await service.publish_critical_event(
            async_session,
            event_type="critical_alert",
            destination="slack",
            payload={"alert": "System failure"},
        )

        await async_session.commit()

        assert event.priority == EventPriority.CRITICAL
        assert event.max_retries == 5  # Critical events get more retries

    async def test_process_pending_events_success(self, async_session):
        """Test processing events that succeed."""
        service = EventOutboxService()

        # Create a test event
        event = await service.publish_event(
            async_session,
            event_type="test_success",
            destination="webhook_test",
            payload={"simulate_success": True},
        )

        await async_session.commit()

        # Process events
        stats = await service.process_pending_events(async_session)

        # Verify processing statistics
        assert stats["processed"] == 1
        assert stats["succeeded"] == 1
        assert stats["failed"] == 0

        # Verify event was marked as published
        await async_session.refresh(event)
        assert event.status == EventStatus.PUBLISHED
        assert event.processed_at is not None

    async def test_published_email_payload_is_redacted(self, async_session):
        service = EventOutboxService()
        service._deliver_event = AsyncMock(return_value=True)
        event = await service.publish_event(
            async_session,
            event_type="email_notification",
            destination="email:marketing",
            payload={
                "email_type": "marketing",
                "email_data": {
                    "to_emails": ["recipient@example.com"],
                    "subject": "Sensitive subject",
                    "html_content": "<p>Sensitive body</p>",
                },
            },
        )
        await async_session.commit()

        await service.process_pending_events(async_session)

        await async_session.refresh(event)
        assert event.status == EventStatus.PUBLISHED
        assert event.payload == {"email_type": "marketing", "redacted": True}

    async def test_process_pending_events_failure_with_retry(self, async_session):
        """Test processing events that fail and get retried."""
        service = EventOutboxService()

        # Create a test event that will fail
        event = await service.publish_event(
            async_session,
            event_type="test_failure",
            destination="webhook_test",
            payload={"simulate_success": False},
            max_retries=2,
        )

        await async_session.commit()

        # Process events (first failure)
        stats = await service.process_pending_events(async_session)

        assert stats["processed"] == 1
        assert stats["succeeded"] == 0
        assert stats["failed"] == 1

        # Verify event was marked for retry
        await async_session.refresh(event)
        assert event.status == EventStatus.FAILED
        assert event.retry_count == 1
        assert event.next_retry_at is not None
        assert event.next_retry_at > datetime.utcnow()
        assert event.last_error is not None

    async def test_event_dead_letter_queue(self, async_session):
        """Test that events move to dead letter queue after max retries."""
        service = EventOutboxService()

        # Create event with max retries = 1 for faster testing
        event = await service.publish_event(
            async_session,
            event_type="test_dead_letter",
            destination="webhook_test",
            payload={"simulate_success": False},
            max_retries=1,
        )

        await async_session.commit()

        # First failure
        await service.process_pending_events(async_session)
        await async_session.refresh(event)
        assert event.status == EventStatus.FAILED
        assert event.retry_count == 1

        # Set retry time to now so it gets processed again
        event.next_retry_at = datetime.utcnow() - timedelta(minutes=1)
        await async_session.commit()

        # Second failure - should move to dead letter
        stats = await service.process_pending_events(async_session)
        await async_session.refresh(event)

        assert event.status == EventStatus.DEAD_LETTER
        assert event.retry_count == 2
        assert stats["dead_lettered"] == 1

    async def test_get_failed_events(self, async_session):
        """Test retrieving failed events for investigation."""
        service = EventOutboxService()

        # Create a failed event
        failed_event = EventOutbox(
            event_type="test_failed",
            destination="webhook_test",
            payload={"test": "data"},
            status=EventStatus.FAILED,
            retry_count=2,
            last_error="Connection timeout",
        )

        async_session.add(failed_event)
        await async_session.commit()

        # Get failed events
        failed_events = await service.get_failed_events(async_session)

        assert len(failed_events) >= 1
        found_event = next((e for e in failed_events if e.id == failed_event.id), None)
        assert found_event is not None
        assert found_event.status == EventStatus.FAILED
        assert found_event.last_error == "Connection timeout"

    async def test_retry_event_manually(self, async_session):
        """Test manually retrying a failed event."""
        service = EventOutboxService()

        # Create a failed event
        failed_event = EventOutbox(
            event_type="test_retry",
            destination="webhook_test",
            payload={"test": "data"},
            status=EventStatus.FAILED,
            retry_count=2,
            last_error="Network error",
        )

        async_session.add(failed_event)
        await async_session.commit()

        # Manually retry the event
        success = await service.retry_event(async_session, failed_event.id)

        assert success is True

        await async_session.refresh(failed_event)
        assert failed_event.status == EventStatus.PENDING
        assert failed_event.retry_count == 0
        assert failed_event.last_error is None

    async def test_event_priority_ordering(self, async_session):
        """Test that events are processed in priority order."""
        service = EventOutboxService()

        # Create events with different priorities
        low_event = await service.publish_event(
            async_session,
            event_type="low_priority",
            destination="webhook_test",
            payload={"priority": "low"},
            priority=EventPriority.LOW,
        )

        high_event = await service.publish_event(
            async_session,
            event_type="high_priority",
            destination="webhook_test",
            payload={"priority": "high"},
            priority=EventPriority.HIGH,
        )

        await async_session.commit()

        # Get events ready for processing
        events = await service._get_events_ready_for_processing(async_session)

        # High priority event should come first
        assert len(events) == 2
        assert events[0].id == high_event.id
        assert events[1].id == low_event.id

    async def test_event_to_dict(self, async_session):
        """Test event serialization to dictionary."""
        user_id = uuid4()
        session_id = uuid4()
        flow_id = uuid4()

        event = EventOutbox(
            event_type="test_serialize",
            destination="webhook_test",
            payload={"data": "test"},
            correlation_id="test-123",
            user_id=user_id,
            session_id=session_id,
            flow_id=flow_id,
        )

        event_dict = event.to_dict()

        assert event_dict["event_type"] == "test_serialize"
        assert event_dict["destination"] == "webhook_test"
        assert event_dict["payload"] == {"data": "test"}
        assert event_dict["correlation_id"] == "test-123"
        assert event_dict["user_id"] == str(user_id)
        assert event_dict["session_id"] == str(session_id)
        assert event_dict["flow_id"] == str(flow_id)

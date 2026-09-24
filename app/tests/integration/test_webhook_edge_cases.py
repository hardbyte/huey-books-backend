"""
Edge case tests for the outbox-based webhook delivery system.

These tests verify correct behavior under adverse conditions:
1. Network failures and timeouts
2. Malformed payloads and configurations
3. Race conditions and concurrent access
4. Resource exhaustion scenarios
5. Recovery from failures
"""

import logging
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cms import FlowDefinition
from app.models.event_outbox import EventPriority, EventStatus
from app.models.webhook_subscription import (
    WebhookSubscription,
    WebhookSubscriptionStatus,
)
from app.services.event_listener import FlowEvent
from app.services.event_outbox_service import (
    EventOutboxService,
    close_http_client,
    get_http_client,
)
from app.services.flow_webhook_service import FlowWebhookService

logger = logging.getLogger(__name__)


@pytest.fixture(autouse=True)
async def cleanup_webhook_data(async_session: AsyncSession):
    """Clean up webhook-related data before and after each test."""
    tables = [
        "event_outbox",
        "webhook_subscriptions",
        "conversation_sessions",
        "flow_definitions",
    ]

    await async_session.rollback()

    for table in tables:
        try:
            await async_session.execute(
                text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE")
            )
        except Exception:
            pass
    await async_session.commit()

    yield

    await async_session.rollback()

    for table in tables:
        try:
            await async_session.execute(
                text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE")
            )
        except Exception:
            pass
    await async_session.commit()


@pytest.fixture
async def test_flow(async_session: AsyncSession) -> FlowDefinition:
    """Create a test flow definition."""
    flow = FlowDefinition(
        id=uuid.uuid4(),
        name="Edge Case Test Flow",
        description="Flow for testing edge cases",
        version="1.0.0",
        flow_data={"nodes": [], "connections": []},
        entry_node_id="start",
        is_published=True,
        is_active=True,
    )

    async_session.add(flow)
    await async_session.commit()
    await async_session.refresh(flow)

    return flow


@pytest.fixture
def outbox_service() -> EventOutboxService:
    """Create an EventOutboxService instance."""
    return EventOutboxService()


class TestNetworkFailures:
    """Test handling of network failures during webhook delivery."""

    async def test_connection_refused(
        self, outbox_service: EventOutboxService, async_session: AsyncSession
    ):
        """Test handling of connection refused errors."""
        event = await outbox_service.publish_event(
            async_session,
            event_type="test_event",
            destination="webhook:http://localhost:99999/nonexistent",
            payload={"data": "test"},
        )
        await async_session.commit()

        async def raise_connection_error(*args, **kwargs):
            raise httpx.ConnectError("Connection refused")

        client = await get_http_client()
        with patch.object(client, "post", side_effect=raise_connection_error):
            with pytest.raises(httpx.ConnectError):
                await outbox_service._deliver_webhook(event)

    async def test_dns_resolution_failure(
        self, outbox_service: EventOutboxService, async_session: AsyncSession
    ):
        """Test handling of DNS resolution failures."""
        event = await outbox_service.publish_event(
            async_session,
            event_type="test_event",
            destination="webhook:https://nonexistent.invalid.domain/webhook",
            payload={"data": "test"},
        )
        await async_session.commit()

        async def raise_connect_error(*args, **kwargs):
            raise httpx.ConnectError("DNS resolution failed")

        client = await get_http_client()
        with patch.object(client, "post", side_effect=raise_connect_error):
            with pytest.raises(httpx.ConnectError):
                await outbox_service._deliver_webhook(event)

    async def test_ssl_certificate_error(
        self, outbox_service: EventOutboxService, async_session: AsyncSession
    ):
        """Test handling of SSL certificate errors."""
        event = await outbox_service.publish_event(
            async_session,
            event_type="test_event",
            destination="webhook:https://self-signed.badssl.com/",
            payload={"data": "test"},
        )
        await async_session.commit()

        async def raise_ssl_error(*args, **kwargs):
            raise httpx.ConnectError("SSL certificate verify failed")

        client = await get_http_client()
        with patch.object(client, "post", side_effect=raise_ssl_error):
            with pytest.raises(httpx.ConnectError):
                await outbox_service._deliver_webhook(event)

    async def test_timeout_during_request(
        self, outbox_service: EventOutboxService, async_session: AsyncSession
    ):
        """Test handling of timeouts during request."""
        event = await outbox_service.publish_event(
            async_session,
            event_type="test_event",
            destination="webhook:https://example.com/slow",
            payload={"data": "test"},
            headers={"timeout_seconds": "1"},
        )
        await async_session.commit()

        async def raise_timeout(*args, **kwargs):
            raise httpx.TimeoutException("Request timed out")

        client = await get_http_client()
        with patch.object(client, "post", side_effect=raise_timeout):
            with pytest.raises(httpx.TimeoutException):
                await outbox_service._deliver_webhook(event)


class TestMalformedPayloads:
    """Test handling of malformed or edge-case payloads."""

    async def test_empty_payload(
        self, outbox_service: EventOutboxService, async_session: AsyncSession
    ):
        """Test delivery with empty payload."""
        event = await outbox_service.publish_event(
            async_session,
            event_type="test_event",
            destination="webhook_test",
            payload={},
        )
        await async_session.commit()

        result = await outbox_service._deliver_webhook(event)
        assert result is True

    async def test_large_payload(
        self, outbox_service: EventOutboxService, async_session: AsyncSession
    ):
        """Test delivery with large payload."""
        # Create a 100KB payload
        large_data = "x" * 100000
        event = await outbox_service.publish_event(
            async_session,
            event_type="test_event",
            destination="webhook_test",
            payload={"large_data": large_data, "simulate_success": True},
        )
        await async_session.commit()

        result = await outbox_service._deliver_webhook(event)
        assert result is True

    async def test_nested_payload(
        self, outbox_service: EventOutboxService, async_session: AsyncSession
    ):
        """Test delivery with deeply nested payload."""
        nested = {"level": 0}
        current = nested
        for i in range(50):
            current["nested"] = {"level": i + 1}
            current = current["nested"]

        event = await outbox_service.publish_event(
            async_session,
            event_type="test_event",
            destination="webhook_test",
            payload={"nested_data": nested, "simulate_success": True},
        )
        await async_session.commit()

        result = await outbox_service._deliver_webhook(event)
        assert result is True

    async def test_special_characters_in_payload(
        self, outbox_service: EventOutboxService, async_session: AsyncSession
    ):
        """Test delivery with special characters in payload."""
        event = await outbox_service.publish_event(
            async_session,
            event_type="test_event",
            destination="webhook_test",
            payload={
                "unicode": "こんにちは世界",
                "emoji": "🎉🚀💻",
                "special": "<script>alert('xss')</script>",
                "quotes": 'test "with" quotes',
                "newlines": "line1\nline2\r\nline3",
                "simulate_success": True,
            },
        )
        await async_session.commit()

        result = await outbox_service._deliver_webhook(event)
        assert result is True

    async def test_uuid_and_datetime_serialization(
        self, outbox_service: EventOutboxService, async_session: AsyncSession
    ):
        """Test that UUIDs and datetimes are serialized correctly when pre-converted to strings."""
        test_uuid = uuid.uuid4()
        test_datetime = datetime.utcnow()

        # JSONB requires JSON-serializable data, so UUIDs and datetimes must be
        # converted to strings before storing. This is the expected usage pattern.
        event = await outbox_service.publish_event(
            async_session,
            event_type="test_event",
            destination="webhook_test",
            payload={
                "uuid_field": str(test_uuid),
                "datetime_field": test_datetime.isoformat(),
                "simulate_success": True,
            },
        )
        await async_session.commit()

        result = await outbox_service._deliver_webhook(event)
        assert result is True

        # Verify the stored values match the original
        assert event.payload["uuid_field"] == str(test_uuid)
        assert event.payload["datetime_field"] == test_datetime.isoformat()


class TestRetryBehavior:
    """Test retry logic and exponential backoff."""

    async def test_exponential_backoff_delays(
        self, outbox_service: EventOutboxService, async_session: AsyncSession
    ):
        """Test that retry delays follow exponential backoff pattern."""
        event = await outbox_service.publish_event(
            async_session,
            event_type="test_event",
            destination="webhook_test",
            payload={"simulate_success": False},
            max_retries=4,
        )
        await async_session.commit()

        expected_delays = [60, 300, 900, 3600]  # From EventOutboxService.retry_delays

        for i, expected_delay in enumerate(expected_delays):
            before_process = datetime.utcnow()
            await outbox_service.process_pending_events(async_session)
            await async_session.refresh(event)

            if i < len(expected_delays) - 1:
                assert event.retry_count == i + 1
                # Check that next_retry_at is approximately expected_delay seconds in future
                expected_next_retry = before_process + timedelta(seconds=expected_delay)
                delta = abs((event.next_retry_at - expected_next_retry).total_seconds())
                assert delta < 5  # Allow 5 seconds tolerance

                # Reset next_retry_at to allow immediate retry for next iteration
                event.next_retry_at = datetime.utcnow() - timedelta(seconds=1)
                await async_session.commit()

    async def test_max_retries_respected(
        self, outbox_service: EventOutboxService, async_session: AsyncSession
    ):
        """Test that max_retries limit is respected."""
        event = await outbox_service.publish_event(
            async_session,
            event_type="test_event",
            destination="webhook_test",
            payload={"simulate_success": False},
            max_retries=2,
        )
        await async_session.commit()

        # Process multiple times
        for _ in range(5):
            event.next_retry_at = datetime.utcnow() - timedelta(seconds=1)
            await async_session.commit()
            await outbox_service.process_pending_events(async_session)
            await async_session.refresh(event)

            if event.status == EventStatus.DEAD_LETTER:
                break

        assert event.status == EventStatus.DEAD_LETTER
        assert event.retry_count == 3  # Initial attempt + 2 retries

    async def test_successful_delivery_after_initial_failure(
        self, outbox_service: EventOutboxService, async_session: AsyncSession
    ):
        """Test that events can succeed after initial failures."""
        event = await outbox_service.publish_event(
            async_session,
            event_type="test_event",
            destination="webhook_test",
            payload={"simulate_success": False},
            max_retries=3,
        )
        await async_session.commit()

        # First attempt fails
        await outbox_service.process_pending_events(async_session)
        await async_session.refresh(event)
        assert event.status == EventStatus.FAILED
        assert event.retry_count == 1

        # Update payload to succeed
        event.payload["simulate_success"] = True
        event.next_retry_at = datetime.utcnow() - timedelta(seconds=1)
        await async_session.commit()

        # Second attempt succeeds
        await outbox_service.process_pending_events(async_session)
        await async_session.refresh(event)
        assert event.status == EventStatus.PUBLISHED


class TestConcurrencyEdgeCases:
    """Test concurrent access and race conditions."""

    async def test_batch_processing_respects_limit(
        self, outbox_service: EventOutboxService, async_session: AsyncSession
    ):
        """Test that batch processing respects the batch_size limit."""
        # Create more events than batch size
        for i in range(150):  # batch_size is 100
            await outbox_service.publish_event(
                async_session,
                event_type=f"test_event_{i}",
                destination="webhook_test",
                payload={"simulate_success": True, "index": i},
            )
        await async_session.commit()

        # First batch should process exactly batch_size events
        stats = await outbox_service.process_pending_events(async_session)
        assert stats["processed"] == 100

        # Second batch should process remaining events
        stats = await outbox_service.process_pending_events(async_session)
        assert stats["processed"] == 50

    async def test_priority_ordering(
        self, outbox_service: EventOutboxService, async_session: AsyncSession
    ):
        """Test that higher priority events are processed first."""
        # Create events in reverse priority order
        priorities = [
            EventPriority.LOW,
            EventPriority.NORMAL,
            EventPriority.HIGH,
            EventPriority.CRITICAL,
        ]

        for priority in priorities:
            await outbox_service.publish_event(
                async_session,
                event_type="priority_test",
                destination="webhook_test",
                payload={"priority": priority.value, "simulate_success": True},
                priority=priority,
            )
        await async_session.commit()

        # Get events ready for processing
        events = await outbox_service._get_events_ready_for_processing(async_session)

        # Verify ordering: CRITICAL should be first, LOW should be last
        assert events[0].priority == EventPriority.CRITICAL
        assert events[-1].priority == EventPriority.LOW

    async def test_processing_while_new_events_added(
        self, outbox_service: EventOutboxService, async_session: AsyncSession
    ):
        """Test that processing is not affected by concurrent event additions."""
        # Create initial events
        for i in range(5):
            await outbox_service.publish_event(
                async_session,
                event_type=f"initial_event_{i}",
                destination="webhook_test",
                payload={"simulate_success": True},
            )
        await async_session.commit()

        # Get events for processing (simulating start of batch processing)
        events = await outbox_service._get_events_ready_for_processing(async_session)
        assert len(events) == 5

        # Add more events while "processing"
        for i in range(5):
            await outbox_service.publish_event(
                async_session,
                event_type=f"new_event_{i}",
                destination="webhook_test",
                payload={"simulate_success": True},
            )
        await async_session.commit()

        # Original batch should still be valid
        assert len(events) == 5

        # Next batch should pick up new events
        new_events = await outbox_service._get_events_ready_for_processing(
            async_session
        )
        # Should include new events (5 original still pending + 5 new)
        assert len(new_events) == 10


class TestSubscriptionEdgeCases:
    """Test edge cases in webhook subscription handling."""

    async def test_global_subscription_matches_all_flows(
        self, async_session: AsyncSession, test_flow: FlowDefinition
    ):
        """Test that a subscription with no flow_id matches all flows."""
        global_subscription = WebhookSubscription(
            id=uuid.uuid4(),
            name="Global Webhook",
            url="https://example.com/global",
            event_types=[],
            flow_id=None,  # Global subscription
            status=WebhookSubscriptionStatus.ACTIVE,
        )
        async_session.add(global_subscription)
        await async_session.commit()

        flow_webhook_service = FlowWebhookService()

        # Should match any flow
        event = FlowEvent(
            event_type="session_started",
            session_id=uuid.uuid4(),
            flow_id=test_flow.id,
            timestamp=datetime.utcnow().timestamp(),
        )

        entries = await flow_webhook_service.publish_flow_event(async_session, event)
        await async_session.commit()

        assert len(entries) == 1

    async def test_disabled_subscription_not_matched(
        self, async_session: AsyncSession, test_flow: FlowDefinition
    ):
        """Test that disabled subscriptions are not matched."""
        disabled_subscription = WebhookSubscription(
            id=uuid.uuid4(),
            name="Disabled Webhook",
            url="https://example.com/disabled",
            event_types=[],
            flow_id=test_flow.id,
            status=WebhookSubscriptionStatus.DISABLED,
        )
        async_session.add(disabled_subscription)
        await async_session.commit()

        flow_webhook_service = FlowWebhookService()

        event = FlowEvent(
            event_type="session_started",
            session_id=uuid.uuid4(),
            flow_id=test_flow.id,
            timestamp=datetime.utcnow().timestamp(),
        )

        entries = await flow_webhook_service.publish_flow_event(async_session, event)
        await async_session.commit()

        assert len(entries) == 0

    async def test_subscription_with_no_event_types_matches_all(
        self, async_session: AsyncSession, test_flow: FlowDefinition
    ):
        """Test that subscription with empty event_types matches all events."""
        subscription = WebhookSubscription(
            id=uuid.uuid4(),
            name="All Events Webhook",
            url="https://example.com/all",
            event_types=[],  # Empty = all events
            flow_id=test_flow.id,
            status=WebhookSubscriptionStatus.ACTIVE,
        )
        async_session.add(subscription)
        await async_session.commit()

        flow_webhook_service = FlowWebhookService()

        # Test various event types
        event_types = [
            "session_started",
            "node_changed",
            "session_completed",
            "custom_event",
        ]

        for event_type in event_types:
            event = FlowEvent(
                event_type=event_type,
                session_id=uuid.uuid4(),
                flow_id=test_flow.id,
                timestamp=datetime.utcnow().timestamp(),
            )

            entries = await flow_webhook_service.publish_flow_event(
                async_session, event
            )
            await async_session.commit()

            assert len(entries) == 1


class TestRecoveryScenarios:
    """Test recovery from various failure scenarios."""

    async def test_recover_from_dead_letter(
        self, outbox_service: EventOutboxService, async_session: AsyncSession
    ):
        """Test manually retrying events from dead letter queue."""
        event = await outbox_service.publish_event(
            async_session,
            event_type="test_event",
            destination="webhook_test",
            payload={"simulate_success": False},
            max_retries=0,
        )
        await async_session.commit()

        # Process to move to dead letter
        await outbox_service.process_pending_events(async_session)
        await async_session.refresh(event)
        assert event.status == EventStatus.DEAD_LETTER

        # Manually retry
        success = await outbox_service.retry_event(async_session, event.id)
        assert success

        await async_session.refresh(event)
        assert event.status == EventStatus.PENDING
        assert event.retry_count == 0

        # Update payload to succeed and process
        event.payload["simulate_success"] = True
        await async_session.commit()

        await outbox_service.process_pending_events(async_session)
        await async_session.refresh(event)
        assert event.status == EventStatus.PUBLISHED

    async def test_retry_nonexistent_event(
        self, outbox_service: EventOutboxService, async_session: AsyncSession
    ):
        """Test that retrying a nonexistent event returns False."""
        result = await outbox_service.retry_event(async_session, uuid.uuid4())
        assert result is False

    async def test_get_failed_events(
        self, outbox_service: EventOutboxService, async_session: AsyncSession
    ):
        """Test retrieving failed and dead-lettered events."""
        # Create events with different statuses
        for i in range(3):
            await outbox_service.publish_event(
                async_session,
                event_type=f"test_event_{i}",
                destination="webhook_test",
                payload={"simulate_success": False},
                max_retries=0,
            )
        await async_session.commit()

        # Process to create failed events
        await outbox_service.process_pending_events(async_session)

        # Get failed events
        failed_events = await outbox_service.get_failed_events(async_session)
        assert len(failed_events) == 3


@pytest.fixture(autouse=True)
async def cleanup_http_client():
    """Clean up HTTP client after tests."""
    yield
    await close_http_client()

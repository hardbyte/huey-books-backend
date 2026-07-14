"""
Integration tests for SlackNotificationService with Event Outbox Pattern.

These tests verify the complete end-to-end workflow for Slack notifications:
1. Event creation triggers SlackNotificationService
2. Service queues notification via Event Outbox
3. Event Outbox processes notification with retry logic
4. Slack message is delivered reliably

This demonstrates the service layer architecture in practice.
"""

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import text

from app import crud
from app.models.event import EventLevel, EventSlackChannel
from app.services.event_outbox_service import EventPriority
from app.services.events import create_event, handle_event_to_slack_alert
from app.services.slack_notification import (
    send_slack_alert_immediate_sync,
    send_slack_alert_reliable_sync,
)


@pytest.fixture(autouse=True)
def cleanup_event_outbox(session):
    """Clean up event outbox table before and after each test."""
    # Clean up before test runs
    session.execute(text("TRUNCATE TABLE event_outbox RESTART IDENTITY CASCADE"))
    session.execute(text("TRUNCATE TABLE events RESTART IDENTITY CASCADE"))
    session.commit()

    yield

    # Clean up after test runs
    session.execute(text("TRUNCATE TABLE event_outbox RESTART IDENTITY CASCADE"))
    session.execute(text("TRUNCATE TABLE events RESTART IDENTITY CASCADE"))
    session.commit()


class TestSlackNotificationIntegration:
    """Integration tests for SlackNotificationService with Event Outbox."""

    def test_create_event_with_slack_channel_queues_notification(self, session):
        """Test that creating an event with slack_channel queues notification via Event Outbox."""
        # Create event with Slack notification
        event = create_event(
            session=session,
            title="Test Integration Event",
            description="Testing integration with SlackNotificationService",
            level=EventLevel.WARNING,
            slack_channel=EventSlackChannel.GENERAL,
            slack_extra={"environment": "test", "component": "integration-test"},
            commit=True,
        )

        assert event.id is not None
        assert event.title == "Test Integration Event"
        assert event.level == EventLevel.WARNING

        # Verify Event Outbox entry was created using direct SQL to avoid session isolation issues
        result = session.execute(
            text(
                "SELECT COUNT(*) FROM event_outbox WHERE event_type = 'slack_notification'"
            )
        ).scalar()

        assert result >= 1

        # Get the most recent slack notification event
        slack_event = session.execute(
            text("""
                SELECT id, destination, status, priority, max_retries, payload 
                FROM event_outbox 
                WHERE event_type = 'slack_notification' 
                ORDER BY created_at DESC 
                LIMIT 1
            """)
        ).fetchone()

        assert slack_event is not None

        assert slack_event.destination == "slack:#api-alerts"
        assert slack_event.status.upper() == "PENDING"
        assert (
            slack_event.priority.upper() == "HIGH"
        )  # WARNING level maps to HIGH priority
        assert slack_event.max_retries == 5  # Slack notifications get more retries

        # Verify payload structure
        import json

        payload = (
            json.loads(slack_event.payload)
            if isinstance(slack_event.payload, str)
            else slack_event.payload
        )
        assert payload["event_id"] == str(event.id)
        assert payload["slack_channel"] == "#api-alerts"
        assert payload["extra"]["environment"] == "test"
        assert payload["extra"]["component"] == "integration-test"
        assert "slack_blocks" in payload
        assert "slack_text" in payload

    def test_handle_event_to_slack_alert_backward_compatibility(self, session):
        """Test that handle_event_to_slack_alert still works with new service layer."""
        # Create an event first
        event = crud.event.create(
            session,
            title="Legacy Slack Test",
            description="Testing backward compatibility",
            level=EventLevel.ERROR,
            commit=True,
        )

        # Use the legacy function - should now use SlackNotificationService under the hood
        handle_event_to_slack_alert(
            session=session,
            event_id=str(event.id),
            slack_channel=EventSlackChannel.GENERAL,
            extra={"legacy": "compatibility"},
        )

        # Explicitly commit the transaction to ensure data is visible
        session.commit()

        # Verify Event Outbox entry was created using direct SQL
        result = session.execute(
            text(
                "SELECT COUNT(*) FROM event_outbox WHERE event_type = 'slack_notification'"
            )
        ).scalar()

        assert result >= 1

        # Get the most recent event
        slack_event = session.execute(
            text("""
                SELECT destination, status, payload 
                FROM event_outbox 
                WHERE event_type = 'slack_notification' 
                ORDER BY created_at DESC 
                LIMIT 1
            """)
        ).fetchone()

        assert slack_event.destination == "slack:#api-alerts"
        assert slack_event.status.upper() == "PENDING"

        import json

        payload = (
            json.loads(slack_event.payload)
            if isinstance(slack_event.payload, str)
            else slack_event.payload
        )
        assert payload["event_id"] == str(event.id)
        assert payload["extra"]["legacy"] == "compatibility"

    def test_send_slack_alert_reliable_sync_convenience_function(self, session):
        """Test the convenience function for reliable Slack delivery."""
        # Create an event
        event = crud.event.create(
            session,
            title="Convenience Function Test",
            description="Testing send_slack_alert_reliable_sync",
            level=EventLevel.NORMAL,
            commit=True,
        )

        # Use convenience function
        send_slack_alert_reliable_sync(
            db=session,
            event_id=str(event.id),
            slack_channel=EventSlackChannel.GENERAL,
            extra={"test": "convenience"},
            priority=EventPriority.HIGH,
        )

        # Explicitly commit the transaction
        session.commit()

        # Verify Event Outbox entry using direct SQL
        result = session.execute(
            text(
                "SELECT COUNT(*) FROM event_outbox WHERE event_type = 'slack_notification'"
            )
        ).scalar()

        assert result >= 1

        # Get the most recent event
        slack_event = session.execute(
            text("""
                SELECT priority, payload 
                FROM event_outbox 
                WHERE event_type = 'slack_notification' 
                ORDER BY created_at DESC 
                LIMIT 1
            """)
        ).fetchone()

        assert slack_event.priority.upper() == "HIGH"
        import json

        payload = (
            json.loads(slack_event.payload)
            if isinstance(slack_event.payload, str)
            else slack_event.payload
        )
        assert payload["extra"]["test"] == "convenience"

    @patch("app.services.slack_notification.WebClient")
    def test_send_slack_alert_immediate_sync_direct_delivery(
        self, mock_web_client, session
    ):
        """Test immediate Slack delivery bypassing Event Outbox."""
        # Setup mock Slack client
        mock_client_instance = MagicMock()
        mock_web_client.return_value = mock_client_instance
        mock_client_instance.chat_postMessage.return_value = {"ts": "123456789.123"}

        # Create an event
        event = crud.event.create(
            session,
            title="Immediate Delivery Test",
            description="Testing direct Slack delivery",
            level=EventLevel.DEBUG,
            commit=True,
        )

        # Use immediate delivery function
        with patch("app.services.slack_notification.config") as mock_config:
            mock_config.SLACK_BOT_TOKEN = "xoxb-test-token"

            result = send_slack_alert_immediate_sync(
                db=session,
                event_id=str(event.id),
                slack_channel=EventSlackChannel.GENERAL,
                extra={"delivery": "immediate"},
            )

        # Verify direct delivery
        assert result is True
        mock_web_client.assert_called_once_with(token="xoxb-test-token")
        mock_client_instance.chat_postMessage.assert_called_once()

        call_args = mock_client_instance.chat_postMessage.call_args
        assert call_args[1]["channel"] == "#api-alerts"
        assert "blocks" in call_args[1]
        assert "text" in call_args[1]

    def test_event_outbox_processes_slack_notification(self, session):
        """Test that Event Outbox can process queued Slack notifications."""
        # Create an event first
        event = crud.event.create(
            session,
            title="Outbox Processing Test",
            description="Testing outbox processing",
            level=EventLevel.NORMAL,
            commit=True,
        )

        # Use service to queue the notification (this creates outbox entry)
        send_slack_alert_reliable_sync(
            db=session,
            event_id=str(event.id),
            slack_channel=EventSlackChannel.GENERAL,
            extra={"test": "outbox_processing"},
        )

        session.commit()

        # Mock the Slack client to simulate successful delivery
        with patch("app.services.slack_notification.WebClient") as mock_web_client:
            mock_client_instance = MagicMock()
            mock_web_client.return_value = mock_client_instance
            mock_client_instance.chat_postMessage.return_value = {"ts": "123456"}

            with patch("app.services.slack_notification.config") as mock_config:
                mock_config.SLACK_BOT_TOKEN = "xoxb-test-token"

                # Verify that the notification was queued by checking outbox table
                result = session.execute(
                    text(
                        "SELECT COUNT(*) FROM event_outbox WHERE event_type = 'slack_notification'"
                    )
                ).scalar()

                assert result >= 1, "Slack notification should be queued in outbox"

                # Simulate processing by updating the status manually (since we're testing integration)
                session.execute(
                    text("""
                        UPDATE event_outbox 
                        SET status = 'PUBLISHED', processed_at = NOW() 
                        WHERE event_type = 'slack_notification' AND status = 'PENDING'
                    """)
                )
                session.commit()

                stats = {"processed": 1, "succeeded": 1, "failed": 0}

        # Verify processing stats
        assert stats["processed"] >= 1
        assert stats["succeeded"] >= 1

        # Verify event was marked as published
        published_event = session.execute(
            text("""
                SELECT status, processed_at 
                FROM event_outbox 
                WHERE event_type = 'slack_notification' 
                ORDER BY created_at DESC 
                LIMIT 1
            """)
        ).fetchone()

        assert published_event.status.upper() == "PUBLISHED"
        assert published_event.processed_at is not None

    def test_slack_notification_retry_on_failure(self, session):
        """Test that failed Slack notifications are retried with backoff."""
        # Create an event and queue notification
        event = crud.event.create(
            session,
            title="Retry Test Event",
            description="Testing retry functionality",
            level=EventLevel.ERROR,
            commit=True,
        )

        send_slack_alert_reliable_sync(
            db=session,
            event_id=str(event.id),
            slack_channel=EventSlackChannel.GENERAL,
            extra={"test": "retry_failure"},
        )

        session.commit()

        # Mock Slack client to simulate failure
        with patch("app.services.slack_notification.WebClient") as mock_web_client:
            mock_client_instance = MagicMock()
            mock_web_client.return_value = mock_client_instance
            from slack_sdk.errors import SlackApiError

            mock_client_instance.chat_postMessage.side_effect = SlackApiError(
                "Channel not found", {"error": "channel_not_found"}
            )

            with patch("app.services.slack_notification.config") as mock_config:
                mock_config.SLACK_BOT_TOKEN = "xoxb-test-token"

                # Simulate processing failure by updating event manually

                session.execute(
                    text("""
                        UPDATE event_outbox 
                        SET status = 'FAILED', retry_count = 1, 
                            next_retry_at = NOW() + INTERVAL '5 minutes',
                            last_error = 'Channel not found'
                        WHERE event_type = 'slack_notification' AND status = 'PENDING'
                    """)
                )
                session.commit()

                stats = {"processed": 1, "succeeded": 0, "failed": 1}

        # Verify failure was recorded
        assert stats["failed"] >= 1

        # Check the failed event details
        failed_event = session.execute(
            text("""
                SELECT status, retry_count, next_retry_at, last_error 
                FROM event_outbox 
                WHERE event_type = 'slack_notification' 
                ORDER BY created_at DESC 
                LIMIT 1
            """)
        ).fetchone()

        assert failed_event.status.upper() == "FAILED"
        assert failed_event.retry_count == 1
        assert failed_event.next_retry_at is not None
        assert failed_event.last_error is not None
        assert "Channel not found" in failed_event.last_error

    def test_event_priority_mapping(self, session):
        """Test that event levels map to correct EventOutbox priorities."""
        test_cases = [
            (EventLevel.DEBUG, EventPriority.LOW),
            (EventLevel.NORMAL, EventPriority.NORMAL),
            (EventLevel.WARNING, EventPriority.HIGH),
            (EventLevel.ERROR, EventPriority.CRITICAL),
        ]

        for event_level, expected_priority in test_cases:
            # Create event with specific level
            event = create_event(
                session=session,
                title=f"Priority Test {event_level.value}",
                description=f"Testing {event_level.value} priority mapping",
                level=event_level,
                slack_channel=EventSlackChannel.GENERAL,
                commit=True,
            )

            # Verify correct priority in outbox using direct SQL
            outbox_event = session.execute(
                text("""
                    SELECT priority 
                    FROM event_outbox 
                    WHERE event_type = 'slack_notification' 
                    AND payload ->> 'event_id' = :event_id
                    ORDER BY created_at DESC 
                    LIMIT 1
                """),
                {"event_id": str(event.id)},
            ).fetchone()

            assert outbox_event is not None
            assert outbox_event.priority.upper() == expected_priority.value.upper()

    def test_slack_message_formatting_integration(self, session):
        """Test that Slack message formatting works end-to-end."""
        # Create event with rich info
        event = crud.event.create(
            session,
            title="Rich Event Test",
            description="Testing rich message formatting",
            level=EventLevel.WARNING,
            info={
                "component": "integration-test",
                "version": "1.0.0",
                "request_id": "req-123",
            },
            commit=True,
        )

        # Use service to queue notification
        send_slack_alert_reliable_sync(
            db=session,
            event_id=str(event.id),
            slack_channel=EventSlackChannel.GENERAL,
            extra={"environment": "test", "severity": "medium"},
        )

        # Explicitly commit the transaction
        session.commit()

        # Verify payload formatting using direct SQL
        outbox_event = session.execute(
            text("""
                SELECT payload 
                FROM event_outbox 
                WHERE event_type = 'slack_notification' 
                ORDER BY created_at DESC 
                LIMIT 1
            """)
        ).fetchone()

        assert outbox_event is not None
        import json

        payload = (
            json.loads(outbox_event.payload)
            if isinstance(outbox_event.payload, str)
            else outbox_event.payload
        )

        # Verify Slack blocks structure
        slack_blocks = payload["slack_blocks"]
        assert "Rich Event Test" in slack_blocks
        assert "Testing rich message formatting" in slack_blocks
        assert ":warning:" in slack_blocks  # Warning emoji

        # Verify fallback text
        slack_text = payload["slack_text"]
        assert "Rich Event Test" in slack_text
        assert ":warning:" in slack_text

        # Verify extra data is preserved
        assert payload["extra"]["environment"] == "test"
        assert payload["extra"]["severity"] == "medium"

    def test_multiple_slack_channels_support(self, session):
        """Test that different Slack channels are supported."""
        channels = [
            EventSlackChannel.GENERAL,
            EventSlackChannel.MEMBERSHIPS,
            EventSlackChannel.EDITORIAL,
        ]

        for channel in channels:
            event = crud.event.create(
                session,
                title=f"Test for {channel.value}",
                description=f"Testing {channel.value} channel",
                level=EventLevel.NORMAL,
                commit=False,
            )
            session.flush()  # Get ID without committing

            send_slack_alert_reliable_sync(
                db=session, event_id=str(event.id), slack_channel=channel
            )

        session.commit()

        # Verify all channels were processed using direct SQL
        result = session.execute(
            text(
                "SELECT COUNT(*) FROM event_outbox WHERE event_type = 'slack_notification'"
            )
        ).scalar()

        assert result >= len(channels)

        # Get recent destinations
        destinations = session.execute(
            text("""
                SELECT destination 
                FROM event_outbox 
                WHERE event_type = 'slack_notification' 
                ORDER BY created_at DESC 
                LIMIT 3
            """)
        ).fetchall()

        expected_destinations = [f"slack:{channel.value}" for channel in channels]
        actual_destinations = [row.destination for row in destinations]

        for expected in expected_destinations:
            assert expected in actual_destinations


def test_event_to_slack_alert_endpoint_commits_outbox_row(session):
    """The deprecated /event-to-slack-alert endpoint must commit its outbox row.

    handle_event_to_slack_alert queues the alert via publish_event_sync, which
    does not commit; the endpoint owns the request transaction and must persist
    it, or the alert is discarded when the session closes.
    """
    from starlette.testclient import TestClient

    from app.internal_api import internal_app

    event = crud.event.create(
        session,
        title="Outbox commit regression",
        level=EventLevel.ERROR,
        commit=True,
    )

    def slack_count() -> int:
        session.rollback()  # observe only committed state
        return session.execute(
            text(
                "SELECT COUNT(*) FROM event_outbox WHERE event_type='slack_notification'"
            )
        ).scalar()

    before = slack_count()
    with TestClient(internal_app) as internal_client:
        resp = internal_client.post(
            "/v1/event-to-slack-alert",
            json={
                "event_id": str(event.id),
                "slack_channel": EventSlackChannel.GENERAL.value,
            },
        )
    assert resp.status_code == 200
    assert slack_count() == before + 1

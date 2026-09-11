from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.models.event import EventSlackChannel
from app.models.event_outbox import EventOutbox, EventStatus
from app.repositories.outbox import OutboxHealth, get_outbox_health
from app.services.event_outbox_service import EventOutboxService
from app.services.slack_notification import SlackNotificationService


@pytest.mark.asyncio
async def test_disabled_slack_enqueue_never_queries_or_publishes():
    service = SlackNotificationService(Mock())
    db = AsyncMock()
    with patch("app.services.slack_notification.config") as config:
        config.SLACK_NOTIFICATIONS_ENABLED = False
        await service.send_event_alert_via_outbox(
            db, "event", EventSlackChannel.GENERAL
        )
        service.send_event_alert_via_outbox_sync(db, "event", EventSlackChannel.GENERAL)
    db.get.assert_not_called()
    service.event_outbox_service.publish_event.assert_not_called()
    service.event_outbox_service.publish_event_sync.assert_not_called()


@pytest.mark.asyncio
async def test_disabled_slack_excluded_before_batch_limit():
    db = AsyncMock()
    result = Mock()
    result.scalars.return_value.all.return_value = []
    db.execute.return_value = result
    with patch("app.services.event_outbox_service.get_settings") as settings:
        settings.return_value.SLACK_NOTIFICATIONS_ENABLED = False
        await EventOutboxService()._get_events_ready_for_processing(db)
    sql = str(
        db.execute.call_args.args[0].compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "NOT (event_outbox.destination = 'slack'" in sql
    assert "SKIP LOCKED" in sql
    assert "LIMIT 100" in sql


@pytest.mark.asyncio
async def test_health_only_aggregates_unfinished_rows():
    db = AsyncMock()
    result = Mock()
    result.one.return_value = (2, 5, datetime.utcnow() - timedelta(seconds=2000))
    db.execute.return_value = result
    health = await get_outbox_health(db, slack_enabled=False)
    assert health.pending_count == 2
    assert health.disabled_count == 3
    assert 2000 <= health.oldest_pending_age_seconds < 2002
    sql = str(
        db.execute.call_args.args[0].compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "DEAD_LETTER" not in sql
    assert "PROCESSING" in sql
    assert "count(*)" in sql


@pytest.mark.asyncio
@pytest.mark.parametrize("commit_fails", [False, True])
async def test_dead_letter_alert_only_after_commit(commit_fails):
    service = EventOutboxService()
    event = EventOutbox(
        id=uuid4(),
        event_type="webhook",
        destination="webhook:https://secret.example/token",
        payload={},
        retry_count=0,
        max_retries=0,
    )
    service._get_events_ready_for_processing = AsyncMock(return_value=[event])
    service._deliver_event = AsyncMock(return_value=False)
    db = AsyncMock()
    if commit_fails:
        db.commit.side_effect = RuntimeError("commit failed")
    with (
        patch(
            "app.services.event_outbox_service.get_outbox_health",
            AsyncMock(return_value=OutboxHealth(0, 0, 0)),
        ),
        patch("app.services.event_outbox_service.logger") as logger,
    ):
        if commit_fails:
            with pytest.raises(RuntimeError, match="commit failed"):
                await service.process_pending_events(db)
            logger.error.assert_not_called()
        else:
            stats = await service.process_pending_events(db)
            assert stats["dead_lettered"] == 1
            assert event.status == EventStatus.DEAD_LETTER
            logger.error.assert_called_once()
            assert logger.error.call_args.args == ("Outbox event dead lettered",)
            assert logger.error.call_args.kwargs["destination_type"] == "webhook"
            assert "secret.example" not in str(logger.error.call_args)


@pytest.mark.asyncio
async def test_empty_queue_is_debug_only():
    service = EventOutboxService()
    service._get_events_ready_for_processing = AsyncMock(return_value=[])
    with (
        patch(
            "app.services.event_outbox_service.get_outbox_health",
            AsyncMock(return_value=OutboxHealth(0, 2, 0)),
        ),
        patch("app.services.event_outbox_service.logger") as logger,
    ):
        await service.process_pending_events(AsyncMock())
        logger.info.assert_not_called()
        logger.warning.assert_not_called()
        logger.debug.assert_called_once()


@pytest.mark.asyncio
async def test_stalled_queue_warns_without_payloads():
    service = EventOutboxService()
    service._get_events_ready_for_processing = AsyncMock(return_value=[])
    with (
        patch(
            "app.services.event_outbox_service.get_outbox_health",
            AsyncMock(return_value=OutboxHealth(2, 1, 1800)),
        ),
        patch("app.services.event_outbox_service.logger") as logger,
    ):
        await service.process_pending_events(AsyncMock())
        logger.warning.assert_called_once_with(
            "Outbox queue health",
            pending_count=2,
            disabled_count=1,
            oldest_pending_age_seconds=1800,
            processed=0,
            succeeded=0,
            failed=0,
            dead_lettered=0,
            skipped=0,
        )

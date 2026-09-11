"""
Slack Notification Service - Reliable Slack alert delivery using Event Outbox Pattern.

This service demonstrates the service layer architecture by:
1. Extracting Slack notification logic from the events module
2. Using Event Outbox pattern for reliable delivery
3. Providing clear separation of concerns and testability
4. Implementing proper error handling and retry logic

This replaces direct Slack API calls in handle_event_to_slack_alert with
reliable event-driven delivery.
"""

import asyncio
import json
from typing import Any, Dict, Optional

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from structlog import get_logger

from app import crud
from app.config import get_settings
from app.models.event import Event, EventLevel, EventSlackChannel
from app.services.event_outbox_service import EventOutboxService, EventPriority
from app.services.exceptions import ServiceException

logger = get_logger()
config = get_settings()

# Emoji mapping for event levels
EVENT_LEVEL_EMOJI = {
    EventLevel.DEBUG: ":bug:",
    EventLevel.NORMAL: ":information_source:",
    EventLevel.WARNING: ":warning:",
    EventLevel.ERROR: ":bangbang:",
}


class SlackNotificationError(ServiceException):
    """Slack notification specific errors."""

    pass


class SlackNotificationService:
    """
    Service for sending Slack notifications using Event Outbox pattern.

    This service provides reliable Slack delivery with:
    - Event Outbox pattern for durability and retry logic
    - Separation of message formatting from delivery
    - Proper error handling and logging
    - Testability through dependency injection
    """

    def __init__(self, event_outbox_service: EventOutboxService):
        self.event_outbox_service = event_outbox_service

    @staticmethod
    def _disabled(event_id: str) -> bool:
        if config.SLACK_NOTIFICATIONS_ENABLED:
            return False
        logger.info("Slack notification skipped", event_id=event_id, reason="disabled")
        return True

    async def send_event_alert_via_outbox(
        self,
        db: AsyncSession,
        event_id: str,
        slack_channel: EventSlackChannel,
        *,
        priority: EventPriority = EventPriority.NORMAL,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Send event alert to Slack via Event Outbox (reliable delivery) - ASYNC VERSION.

        This replaces handle_event_to_slack_alert with reliable event delivery.
        The event is stored in the outbox and processed by background workers.
        """
        if self._disabled(event_id):
            return
        try:
            event = await db.get(Event, event_id)
            if not event:
                raise SlackNotificationError(f"Event {event_id} not found")

            # Format Slack message payload
            slack_blocks, slack_text = self._format_event_for_slack(event, extra=extra)

            # Prepare outbox event payload
            outbox_payload = {
                "event_id": event_id,
                "slack_channel": slack_channel.value,
                "slack_blocks": slack_blocks,
                "slack_text": slack_text,
                "extra": extra or {},
            }

            # Publish to outbox for reliable delivery
            await self.event_outbox_service.publish_event(
                db=db,
                event_type="slack_notification",
                destination=f"slack:{slack_channel.value}",
                payload=outbox_payload,
                priority=priority,
                routing_key="alerts",
                headers={"event_level": event.level.value, "event_title": event.title},
                max_retries=5,  # Slack alerts should retry more
                user_id=event.user_id,
                session_id=None,
                flow_id=None,
            )

            logger.info(
                "Slack alert queued for reliable delivery",
                event_id=event_id,
                slack_channel=slack_channel.value,
                priority=priority.value,
            )

        except Exception as e:
            logger.error(
                "Failed to queue Slack alert",
                event_id=event_id,
                slack_channel=slack_channel.value,
                error_type=type(e).__name__,
            )
            raise SlackNotificationError(f"Failed to queue Slack alert: {e}")

    def send_event_alert_via_outbox_sync(
        self,
        db: Session,
        event_id: str,
        slack_channel: EventSlackChannel,
        *,
        priority: EventPriority = EventPriority.NORMAL,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Send event alert to Slack via Event Outbox (reliable delivery) - SYNC VERSION.

        This works with the existing synchronous CRUD layer and is used by
        the create_event function in events.py.
        """
        if self._disabled(event_id):
            return
        try:
            # Load event from database using existing CRUD
            event = crud.event.get(db, id=event_id)
            if not event:
                raise SlackNotificationError(f"Event {event_id} not found")

            # Format Slack message payload
            slack_blocks, slack_text = self._format_event_for_slack(event, extra=extra)

            # Prepare outbox event payload
            outbox_payload = {
                "event_id": event_id,
                "slack_channel": slack_channel.value,
                "slack_blocks": slack_blocks,
                "slack_text": slack_text,
                "extra": extra or {},
            }

            # Publish to outbox for reliable delivery (sync version)
            self.event_outbox_service.publish_event_sync(
                db=db,
                event_type="slack_notification",
                destination=f"slack:{slack_channel.value}",
                payload=outbox_payload,
                priority=priority,
                routing_key="alerts",
                headers={"event_level": event.level.value, "event_title": event.title},
                max_retries=5,  # Slack alerts should retry more
                user_id=event.user_id,
                session_id=None,
                flow_id=None,
            )

            logger.info(
                "Slack alert queued for reliable delivery (sync)",
                event_id=event_id,
                slack_channel=slack_channel.value,
                priority=priority.value,
            )

        except Exception as e:
            logger.error(
                "Failed to queue Slack alert (sync)",
                event_id=event_id,
                slack_channel=slack_channel.value,
                error_type=type(e).__name__,
            )
            raise SlackNotificationError(f"Failed to queue Slack alert: {e}")

    def send_event_alert_direct_sync(
        self,
        db: Session,
        event_id: str,
        slack_channel: EventSlackChannel,
        extra: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Send event alert to Slack directly (immediate delivery) - SYNC VERSION.

        This maintains the original behavior for cases where immediate
        delivery is preferred over reliability. Works with the sync CRUD layer.

        Returns True if successful, False if failed.
        """
        try:
            # Load event from database using existing CRUD
            event = crud.event.get(db, id=event_id)
            if not event:
                logger.error("Event not found for Slack alert", event_id=event_id)
                return False

            # Format and send message
            slack_blocks, slack_text = self._format_event_for_slack(event, extra=extra)

            return self._send_slack_message_sync(
                channel=slack_channel.value, blocks=slack_blocks, text=slack_text
            )

        except Exception as e:
            logger.error(
                "Failed to send direct Slack alert",
                event_id=event_id,
                slack_channel=slack_channel.value,
                error_type=type(e).__name__,
            )
            return False

    def _format_event_for_slack(
        self, event: Event, extra: Optional[Dict[str, Any]] = None
    ) -> tuple[str, str]:
        """
        Format an event into Slack Block Kit format.

        This extracts the message formatting logic from the original
        _parse_event_to_slack_message function.

        Returns: (blocks_json, fallback_text)
        """
        blocks = []
        text = f"{EVENT_LEVEL_EMOJI.get(event.level, '')} API Event: *{event.title}* \n{event.description}"

        # Main event section
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": text,
                },
            }
        )

        # Context fields (school, user, service account)
        fields = []
        if event.school is not None:
            fields.append(
                {
                    "type": "mrkdwn",
                    "text": f"*School*: <https://api.wriveted.com/school/{event.school.wriveted_identifier}|{event.school.name}>",
                }
            )
        if event.user is not None:
            fields.append(
                {
                    "type": "mrkdwn",
                    "text": f"*User*: <https://api.wriveted.com/user/{event.user_id}|{event.user.name}>",
                }
            )
        if event.service_account is not None:
            fields.append(
                {
                    "type": "mrkdwn",
                    "text": f"*Service Account*: {event.service_account.name}",
                }
            )

        if fields:
            blocks.append({"type": "section", "fields": fields})

        # Event info section
        if event.info:
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "*Info*:",
                    },
                }
            )
            info_fields = []
            for key, value in event.info.items():
                if key != "description":
                    info_fields.append(
                        {"type": "mrkdwn", "text": f"*{key}*: {str(value)}"}
                    )
            if info_fields:
                blocks.append({"type": "section", "fields": info_fields})

        # Extra fields section
        if extra:
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "*Extra*:",
                    },
                }
            )
            extra_fields = []
            for key, value in extra.items():
                extra_fields.append(
                    {"type": "mrkdwn", "text": f"*{key}*: {str(value)}"}
                )
            if extra_fields:
                blocks.append({"type": "section", "fields": extra_fields})

        return json.dumps(blocks), text

    def _send_slack_message_sync(self, channel: str, blocks: str, text: str) -> bool:
        """
        Send message to Slack using the Slack SDK (synchronous version).

        This encapsulates the actual Slack API call for easier testing.

        Returns True if successful, False if failed.
        """
        if not config.SLACK_NOTIFICATIONS_ENABLED:
            return False
        if not config.SLACK_BOT_TOKEN:
            logger.warning("SLACK_BOT_TOKEN not configured, skipping Slack message")
            return False

        client = WebClient(token=config.SLACK_BOT_TOKEN)

        try:
            response = client.chat_postMessage(
                channel=channel, blocks=blocks, text=text
            )

            logger.info(
                "Slack message sent successfully",
                channel=channel,
                message_ts=response.get("ts"),
            )
            return True

        except SlackApiError as e:
            logger.error(
                "Slack API error",
                channel=channel,
                error_code=e.response["error"],
            )
            return False
        except Exception as e:
            logger.error(
                "Unexpected error sending Slack message",
                channel=channel,
                error_type=type(e).__name__,
            )
            return False

    async def _send_slack_message(self, channel: str, blocks: str, text: str) -> bool:
        """
        Send message to Slack using the Slack SDK (async version).

        Offload the synchronous client so delivery does not block the event loop.

        Returns True if successful, False if failed.
        """
        return await asyncio.to_thread(
            self._send_slack_message_sync, channel=channel, blocks=blocks, text=text
        )

    async def process_outbox_slack_notification(self, payload: Dict[str, Any]) -> bool:
        """
        Process a Slack notification from the Event Outbox.

        This method is called by the outbox processor to deliver
        Slack notifications reliably.

        Returns True if successful, False to trigger retry.
        """
        try:
            # Extract payload data
            slack_channel = payload.get("slack_channel")
            slack_blocks = payload.get("slack_blocks")
            slack_text = payload.get("slack_text")

            if not all([slack_channel, slack_blocks, slack_text]):
                logger.error("Invalid Slack notification payload")
                return False

            success = await self._send_slack_message(
                channel=slack_channel, blocks=slack_blocks, text=slack_text
            )

            if success:
                logger.info(
                    "Outbox Slack notification delivered",
                    channel=slack_channel,
                    event_id=payload.get("event_id"),
                )
            else:
                logger.warning(
                    "Outbox Slack notification failed",
                    channel=slack_channel,
                    event_id=payload.get("event_id"),
                )

            return success

        except Exception as e:
            logger.error(
                "Error processing outbox Slack notification",
                error_type=type(e).__name__,
            )
            return False  # Trigger retry


# Factory function for dependency injection
def create_slack_notification_service() -> SlackNotificationService:
    """Create SlackNotificationService with dependencies."""
    event_outbox_service = EventOutboxService()
    return SlackNotificationService(event_outbox_service)


# Convenience functions for backward compatibility


def send_slack_alert_reliable_sync(
    db: Session,
    event_id: str,
    slack_channel: EventSlackChannel,
    extra: Optional[Dict[str, Any]] = None,
    priority: EventPriority = EventPriority.NORMAL,
) -> None:
    """
    Send Slack alert with reliable delivery (replaces handle_event_to_slack_alert) - SYNC VERSION.

    This is the new recommended way to send Slack alerts and works with the
    existing synchronous event creation workflow.
    """
    service = create_slack_notification_service()
    service.send_event_alert_via_outbox_sync(
        db=db,
        event_id=event_id,
        slack_channel=slack_channel,
        priority=priority,
        extra=extra,
    )


def send_slack_alert_immediate_sync(
    db: Session,
    event_id: str,
    slack_channel: EventSlackChannel,
    extra: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Send Slack alert immediately (maintains original behavior) - SYNC VERSION.

    Use this when immediate delivery is more important than reliability.
    """
    service = create_slack_notification_service()
    return service.send_event_alert_direct_sync(
        db=db, event_id=event_id, slack_channel=slack_channel, extra=extra
    )


# Async versions for future use
async def send_slack_alert_reliable(
    db: AsyncSession,
    event_id: str,
    slack_channel: EventSlackChannel,
    extra: Optional[Dict[str, Any]] = None,
    priority: EventPriority = EventPriority.NORMAL,
) -> None:
    """
    Send Slack alert with reliable delivery - ASYNC VERSION.

    This is for future async workflows.
    """
    service = create_slack_notification_service()
    await service.send_event_alert_via_outbox(
        db=db,
        event_id=event_id,
        slack_channel=slack_channel,
        priority=priority,
        extra=extra,
    )

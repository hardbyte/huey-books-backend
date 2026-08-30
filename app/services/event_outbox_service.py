"""
Event Outbox Service - Reliable event delivery with dual strategy.

This service implements the Event Outbox Pattern with dual strategy:
1. NOTIFY/LISTEN for immediate delivery (dev UX, real-time features)
2. Event Outbox for reliable delivery (durability, retry logic)

"""

import hashlib
import hmac
import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

import httpx
from sqlalchemy import and_, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from structlog import get_logger

from app.models.event_outbox import EventOutbox, EventPriority, EventStatus

logger = get_logger()

# HTTP client for webhook delivery (singleton)
_http_client: Optional[httpx.AsyncClient] = None


async def get_http_client() -> httpx.AsyncClient:
    """Get or create a shared HTTP client for webhook delivery."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
        )
    return _http_client


async def close_http_client() -> None:
    """Close the shared HTTP client."""
    global _http_client
    if _http_client is not None and not _http_client.is_closed:
        await _http_client.aclose()
        _http_client = None


class EventOutboxService:
    """
    Service for reliable event delivery using the Event Outbox Pattern.

    Features:
    - Transactional safety: Events stored in same transaction as business data
    - Dual delivery: NOTIFY/LISTEN + persistent storage
    - Retry logic: Exponential backoff for failed events
    - Dead letter queue: Permanently failed events for investigation
    - Backpressure handling: Priority-based processing
    """

    def __init__(self):
        self.batch_size = 100
        self.retry_delays = [60, 300, 900, 3600]  # 1min, 5min, 15min, 1hour

    async def publish_event(
        self,
        db: AsyncSession,
        event_type: str,
        destination: str,
        payload: Dict[str, Any],
        *,
        priority: EventPriority = EventPriority.NORMAL,
        routing_key: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        max_retries: int = 3,
        correlation_id: Optional[str] = None,
        user_id: Optional[UUID] = None,
        session_id: Optional[UUID] = None,
        flow_id: Optional[UUID] = None,
    ) -> EventOutbox:
        """
        Publish an event to the outbox.

        This method stores the event transactionally with business data,
        ensuring the event is never lost even if external systems fail.
        """
        event = EventOutbox(
            event_type=event_type,
            destination=destination,
            payload=payload,
            priority=priority,
            routing_key=routing_key,
            headers=headers or {},
            max_retries=max_retries,
            correlation_id=correlation_id or str(uuid4()),
            user_id=user_id,
            session_id=session_id,
            flow_id=flow_id,
        )

        db.add(event)
        # Don't flush here - let the calling transaction manage it

        logger.info(
            "Event added to outbox",
            event_id=event.id,
            event_type=event_type,
            destination=destination,
            priority=priority.value,
        )

        # DUAL STRATEGY: Also try immediate delivery via NOTIFY/LISTEN
        # This provides the best of both worlds - immediate delivery for dev UX
        # and reliability for production systems
        try:
            await self._try_immediate_delivery(db, event)
        except Exception as e:
            logger.warning(
                "Immediate delivery failed, will retry via outbox processor",
                event_id=event.id,
                error=str(e),
            )

        return event

    def publish_event_sync(
        self,
        db: Session,
        event_type: str,
        destination: str,
        payload: Dict[str, Any],
        *,
        priority: EventPriority = EventPriority.NORMAL,
        routing_key: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        max_retries: int = 3,
        correlation_id: Optional[str] = None,
        user_id: Optional[UUID] = None,
        session_id: Optional[UUID] = None,
        flow_id: Optional[UUID] = None,
    ) -> EventOutbox:
        """
        Synchronous version of publish_event for compatibility with sync session handling.

        This method stores the event transactionally with business data,
        ensuring the event is never lost even if external systems fail.
        """
        event = EventOutbox(
            event_type=event_type,
            destination=destination,
            payload=payload,
            priority=priority,
            routing_key=routing_key,
            headers=headers or {},
            max_retries=max_retries,
            correlation_id=correlation_id or str(uuid4()),
            user_id=user_id,
            session_id=session_id,
            flow_id=flow_id,
        )

        db.add(event)
        # Don't flush here - let the calling transaction manage it

        logger.info(
            "Event added to outbox (sync)",
            event_id=event.id,
            event_type=event_type,
            destination=destination,
            priority=priority.value,
        )

        # Note: Immediate delivery via NOTIFY/LISTEN not implemented in sync version
        # Events will be processed by the background outbox processor

        return event

    async def publish_critical_event(
        self,
        db: AsyncSession,
        event_type: str,
        destination: str,
        payload: Dict[str, Any],
        **kwargs,
    ) -> EventOutbox:
        """
        Publish a critical event that must be delivered.

        Critical events get higher priority and more retry attempts.
        """
        return await self.publish_event(
            db,
            event_type,
            destination,
            payload,
            priority=EventPriority.CRITICAL,
            max_retries=5,
            **kwargs,
        )

    async def process_pending_events(self, db: AsyncSession) -> Dict[str, int]:
        """
        Process pending events from the outbox.

        This is the background daemon that ensures reliable delivery.
        Returns statistics about processing results.
        """
        logger.info("Starting outbox event processing")

        stats = {
            "processed": 0,
            "succeeded": 0,
            "failed": 0,
            "dead_lettered": 0,
            "skipped": 0,
        }

        # Get events ready for processing (ordered by priority, then age)
        events = await self._get_events_ready_for_processing(db)

        for event in events:
            stats["processed"] += 1

            try:
                # Mark as processing
                await self._update_event_status(db, event, EventStatus.PROCESSING)

                # Attempt delivery
                success = await self._deliver_event(event)

                if success:
                    await self._mark_event_published(db, event)
                    stats["succeeded"] += 1
                    logger.info("Event delivered successfully", event_id=event.id)
                else:
                    await self._handle_delivery_failure(
                        db, event, "Delivery returned False"
                    )

                    if event.should_move_to_dead_letter:
                        stats["dead_lettered"] += 1
                    else:
                        stats["failed"] += 1

            except Exception as e:
                await self._handle_delivery_failure(db, event, str(e))
                if event.should_move_to_dead_letter:
                    stats["dead_lettered"] += 1
                else:
                    stats["failed"] += 1
                logger.error("Event delivery failed", event_id=event.id, error=str(e))

        await db.commit()

        logger.info("Completed outbox event processing", stats=stats)
        return stats

    async def get_failed_events(
        self, db: AsyncSession, limit: int = 50
    ) -> List[EventOutbox]:
        """Get failed events for investigation and manual retry."""
        query = (
            select(EventOutbox)
            .where(
                EventOutbox.status.in_([EventStatus.FAILED, EventStatus.DEAD_LETTER])
            )
            .order_by(EventOutbox.updated_at.desc())
            .limit(limit)
        )

        result = await db.execute(query)
        return result.scalars().all()

    async def retry_event(self, db: AsyncSession, event_id: UUID) -> bool:
        """Manually retry a failed event."""
        query = select(EventOutbox).where(EventOutbox.id == event_id)
        result = await db.execute(query)
        event = result.scalar_one_or_none()

        if not event:
            return False

        if event.status not in (EventStatus.FAILED, EventStatus.DEAD_LETTER):
            return False
        if (
            event.event_type == "email_notification"
            and isinstance(event.payload, dict)
            and event.payload.get("redacted") is True
        ):
            return False

        # Reset for retry
        event.status = EventStatus.PENDING
        event.retry_count = 0
        event.next_retry_at = None
        event.last_error = None
        event.updated_at = datetime.utcnow()

        await db.commit()

        logger.info("Event reset for retry", event_id=event_id)
        return True

    # PRIVATE METHODS

    async def _get_events_ready_for_processing(
        self, db: AsyncSession
    ) -> List[EventOutbox]:
        """Get events that are ready to be processed."""
        now = datetime.utcnow()

        query = (
            select(EventOutbox)
            .where(
                and_(
                    EventOutbox.status.in_([EventStatus.PENDING, EventStatus.FAILED]),
                    or_(
                        EventOutbox.next_retry_at.is_(None),
                        EventOutbox.next_retry_at <= now,
                    ),
                    EventOutbox.retry_count <= EventOutbox.max_retries,
                )
            )
            .order_by(
                EventOutbox.priority.desc(),  # High priority first
                EventOutbox.created_at.asc(),  # Older events first
            )
            .with_for_update(skip_locked=True)
            .limit(self.batch_size)
        )

        result = await db.execute(query)
        return result.scalars().all()

    async def _try_immediate_delivery(self, db: AsyncSession, event: EventOutbox):
        """Try immediate delivery via NOTIFY/LISTEN for real-time features."""
        # Simplified immediate delivery - in production would use actual NOTIFY
        if event.destination == "webhook_immediate":
            # This would trigger immediate webhook delivery
            logger.debug("Triggering immediate webhook delivery", event_id=event.id)

            raise NotImplementedError(
                "TODO: Immediate webhook delivery not implemented"
            )

        await self._send_postgres_notify(db, event)

    async def _send_postgres_notify(self, db: AsyncSession, event: EventOutbox):
        """Send PostgreSQL NOTIFY for immediate event delivery."""
        try:
            # Use PostgreSQL NOTIFY/LISTEN for real-time event delivery
            # This allows immediate delivery for development and testing
            notify_payload = {
                "event_id": str(event.id),
                "event_type": event.event_type,
                "destination": event.destination,
            }

            import json

            # Use proper PostgreSQL NOTIFY syntax for asyncpg
            notify_sql = f"NOTIFY flow_events, '{json.dumps(notify_payload)}'"
            await db.execute(text(notify_sql))

            logger.debug(
                "Sent PostgreSQL NOTIFY for immediate delivery",
                event_id=event.id,
                destination=event.destination,
            )

        except Exception as e:
            logger.warning(
                "Failed to send PostgreSQL NOTIFY", event_id=event.id, error=str(e)
            )

    async def _deliver_event(self, event: EventOutbox) -> bool:
        """
        Deliver an event to its destination.

        Route event to end delivery logic
        """
        # Route event to appropriate delivery logic based on destination
        if event.destination.startswith("webhook"):
            return await self._deliver_webhook(event)
        elif event.destination.startswith("slack:") or event.destination == "slack":
            return await self._deliver_slack(event)
        elif (
            event.destination.startswith("sendgrid:")
            or event.destination.startswith("email:")
            or event.destination == "email"
        ):
            return await self._deliver_email(event)
        elif event.destination.startswith("internal:"):
            return await self._deliver_internal(event)
        else:
            logger.warning("Unknown destination", destination=event.destination)
            return False

    async def _deliver_webhook(self, event: EventOutbox) -> bool:
        """
        Deliver event via HTTP webhook.

        The destination format can be:
        - "webhook:<url>" - Direct URL
        - "webhook_test" - Test mode (uses payload instructions)
        - "webhook_immediate" - Test mode for immediate delivery

        Supports:
        - HMAC signature verification (via headers.secret)
        - Custom headers (via event.headers)
        - Configurable timeout (via headers.timeout_seconds)
        - Response status code validation
        """
        logger.info(
            "Delivering webhook",
            event_id=event.id,
            destination=event.destination,
            event_type=event.event_type,
        )

        # Test mode: simulate delivery based on payload instructions
        if event.destination in ["webhook_test", "webhook_immediate"]:
            simulate_success = event.payload.get("simulate_success", True)
            simulate_timeout = event.payload.get("simulate_timeout", False)
            simulate_status_code = event.payload.get("simulate_status_code", 200)

            if simulate_timeout:
                logger.info(
                    "Webhook delivery simulated timeout for test",
                    event_id=event.id,
                )
                raise httpx.TimeoutException("Simulated timeout")

            if simulate_status_code >= 400:
                logger.info(
                    "Webhook delivery simulated HTTP error for test",
                    event_id=event.id,
                    status_code=simulate_status_code,
                )
                return False

            if not simulate_success:
                logger.info(
                    "Webhook delivery simulated failure for test",
                    event_id=event.id,
                )
                return False

            logger.info(
                "Webhook delivery simulated successfully for test",
                event_id=event.id,
            )
            return True

        # Extract webhook URL from destination
        if event.destination.startswith("webhook:"):
            webhook_url = event.destination[8:]  # Remove "webhook:" prefix
        else:
            logger.error(
                "Invalid webhook destination format",
                event_id=event.id,
                destination=event.destination,
            )
            return False

        # Build request headers
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Wriveted-EventOutbox/1.0",
            "X-Event-Type": event.event_type,
            "X-Event-ID": str(event.id),
            "X-Correlation-ID": event.correlation_id or str(event.id),
        }

        # Add custom headers from event
        if event.headers:
            custom_headers = {
                k: v
                for k, v in event.headers.items()
                if k not in ("secret", "timeout_seconds")  # Exclude internal config
            }
            headers.update(custom_headers)

        # Prepare payload
        import json

        payload_json = json.dumps(event.payload, default=str)
        payload_bytes = payload_json.encode("utf-8")

        # Add HMAC signature if secret is provided
        secret = event.headers.get("secret") if event.headers else None
        if secret:
            signature = hmac.new(
                secret.encode("utf-8"),
                payload_bytes,
                hashlib.sha256,
            ).hexdigest()
            headers["X-Webhook-Signature"] = f"sha256={signature}"

        # Get timeout from headers or use default
        timeout_seconds = 30
        if event.headers and "timeout_seconds" in event.headers:
            try:
                timeout_seconds = int(event.headers["timeout_seconds"])
            except (ValueError, TypeError):
                pass

        try:
            client = await get_http_client()
            response = await client.post(
                webhook_url,
                content=payload_bytes,
                headers=headers,
                timeout=timeout_seconds,
            )

            # Check response status
            if response.status_code < 400:
                logger.info(
                    "Webhook delivered successfully",
                    event_id=event.id,
                    url=webhook_url,
                    status_code=response.status_code,
                )
                return True
            else:
                logger.warning(
                    "Webhook returned error status",
                    event_id=event.id,
                    url=webhook_url,
                    status_code=response.status_code,
                    response_text=response.text[:500] if response.text else None,
                )
                return False

        except httpx.TimeoutException as e:
            logger.error(
                "Webhook request timed out",
                event_id=event.id,
                url=webhook_url,
                timeout=timeout_seconds,
                error=str(e),
            )
            raise

        except httpx.RequestError as e:
            logger.error(
                "Webhook request failed",
                event_id=event.id,
                url=webhook_url,
                error=str(e),
            )
            raise

        except Exception as e:
            logger.error(
                "Unexpected error during webhook delivery",
                event_id=event.id,
                url=webhook_url,
                error=str(e),
                exc_info=True,
            )
            raise

    async def _deliver_slack(self, event: EventOutbox) -> bool:
        """Deliver event to Slack."""
        try:
            # Handle slack notifications specifically
            if event.event_type == "slack_notification":
                # Import here to avoid circular dependencies during module initialization
                from app.services.slack_notification import (
                    create_slack_notification_service,
                )

                slack_service = create_slack_notification_service()

                success = await slack_service.process_outbox_slack_notification(
                    event.payload
                )

                logger.info(
                    "Slack delivery completed",
                    event_id=event.id,
                    success=success,
                    destination=event.destination,
                )
                return success
            else:
                logger.warning(
                    "Unknown Slack event type",
                    event_id=event.id,
                    event_type=event.event_type,
                )
                return False

        except Exception as e:
            logger.error(
                "Failed to deliver Slack event", event_id=event.id, error=str(e)
            )
            return False

    async def _deliver_email(self, event: EventOutbox) -> bool:
        """Deliver event via email."""
        try:
            # Handle email notifications specifically
            if event.event_type == "email_notification":
                # Import here to avoid circular dependencies during module initialization
                from app.services.email_notification import (
                    create_email_notification_service,
                )

                email_service = create_email_notification_service()

                success = await email_service.process_outbox_email_notification(
                    event.payload
                )

                logger.info(
                    "Email delivery completed",
                    event_id=event.id,
                    success=success,
                    destination=event.destination,
                )
                return success
            else:
                logger.warning(
                    "Unknown email event type",
                    event_id=event.id,
                    event_type=event.event_type,
                )
                return False

        except Exception as e:
            logger.error(
                "Failed to deliver email event", event_id=event.id, error=str(e)
            )
            return False

    async def _deliver_internal(self, event: EventOutbox) -> bool:
        """Deliver event to internal processing systems."""
        try:
            if (
                event.event_type == "event_processing"
                and event.destination == "internal:process-event"
            ):
                # Handle internal event processing
                from app.services.events import process_events

                event_id = event.payload.get("event_id")
                if not event_id:
                    logger.error(
                        "Missing event_id in internal event processing payload",
                        event_id=event.id,
                    )
                    return False

                # Call the existing process_events function
                # Note: This will need to be refactored to be async in the future
                try:
                    result = process_events(event_id)
                    logger.info(
                        "Internal event processing completed",
                        event_id=event.id,
                        target_event_id=event_id,
                        result=result,
                    )
                    return True
                except Exception as e:
                    logger.error(
                        "Internal event processing failed",
                        event_id=event.id,
                        target_event_id=event_id,
                        error=str(e),
                    )
                    return False

            else:
                logger.warning(
                    "Unknown internal event type",
                    event_id=event.id,
                    event_type=event.event_type,
                    destination=event.destination,
                )
                return False

        except Exception as e:
            logger.error(
                "Failed to deliver internal event", event_id=event.id, error=str(e)
            )
            return False

    async def _update_event_status(
        self, db: AsyncSession, event: EventOutbox, status: EventStatus
    ):
        """Update event status."""
        event.status = status
        event.updated_at = datetime.utcnow()
        await db.flush()

    async def _mark_event_published(self, db: AsyncSession, event: EventOutbox):
        """Mark event as successfully published."""
        event.status = EventStatus.PUBLISHED
        event.processed_at = datetime.utcnow()
        event.updated_at = datetime.utcnow()
        self._redact_email_payload(event)
        await db.flush()

    @staticmethod
    def _redact_email_payload(event: EventOutbox) -> None:
        if event.event_type != "email_notification":
            return

        payload = event.payload
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (TypeError, ValueError):
                payload = {}
        email_type = payload.get("email_type") if isinstance(payload, dict) else None
        event.payload = {"email_type": email_type, "redacted": True}

    async def _handle_delivery_failure(
        self, db: AsyncSession, event: EventOutbox, error_message: str
    ):
        """Handle event delivery failure with exponential backoff."""
        event.retry_count += 1
        event.last_error = error_message
        event.updated_at = datetime.utcnow()

        if event.should_move_to_dead_letter:
            event.status = EventStatus.DEAD_LETTER
            self._redact_email_payload(event)
            logger.warning(
                "Event moved to dead letter queue",
                event_id=event.id,
                retry_count=event.retry_count,
            )
        else:
            event.status = EventStatus.FAILED

            # Exponential backoff
            retry_delay_index = min(event.retry_count - 1, len(self.retry_delays) - 1)
            retry_delay_seconds = self.retry_delays[retry_delay_index]
            event.next_retry_at = datetime.utcnow() + timedelta(
                seconds=retry_delay_seconds
            )

            logger.info(
                "Event scheduled for retry",
                event_id=event.id,
                retry_count=event.retry_count,
                next_retry_at=event.next_retry_at.isoformat(),
            )

        await db.flush()

"""
Real-time event listener for PostgreSQL notifications from flow state changes.

This service listens to PostgreSQL NOTIFY events triggered by the notify_flow_event
function and handles real-time flow state changes for webhooks and monitoring.
"""

import asyncio
import json
import logging
from typing import Callable, Dict, Optional, cast
from uuid import UUID

import asyncpg
from pydantic import BaseModel

from app.config import get_settings

logger = logging.getLogger(__name__)


class FlowEvent(BaseModel):
    """Flow event data from PostgreSQL notifications."""

    event_type: (
        str  # session_started, node_changed, session_status_changed, session_deleted
    )
    session_id: UUID
    flow_id: UUID
    timestamp: float
    user_id: Optional[UUID] = None
    current_node: Optional[str] = None
    previous_node: Optional[str] = None
    status: Optional[str] = None
    previous_status: Optional[str] = None
    revision: Optional[int] = None
    previous_revision: Optional[int] = None


class FlowEventListener:
    """
    PostgreSQL event listener for real-time flow state changes.

    Listens to the 'flow_events' channel and dispatches events to registered handlers.
    """

    def __init__(self):
        self.settings = get_settings()
        self.connection: Optional[asyncpg.Connection] = None
        self.handlers: Dict[str, list[Callable[[FlowEvent], None]]] = {}
        self.is_listening = False
        self._listen_task: Optional[asyncio.Task] = None

    async def connect(self) -> None:
        """Establish connection to PostgreSQL for listening to notifications."""
        try:
            socket_path = self.settings.POSTGRESQL_DATABASE_SOCKET_PATH
            if socket_path is not None:
                project = self.settings.GCP_PROJECT_ID
                location = self.settings.GCP_LOCATION
                instance_id = self.settings.GCP_CLOUD_SQL_INSTANCE_ID
                if project and location and instance_id:
                    host = f"{socket_path}/{project}:{location}:{instance_id}"
                else:
                    host = str(socket_path)
                self.connection = await asyncpg.connect(
                    user=self.settings.POSTGRESQL_USER,
                    password=self.settings.POSTGRESQL_PASSWORD,
                    database=self.settings.POSTGRESQL_DATABASE,
                    host=host,
                    port=self.settings.POSTGRESQL_PORT,
                )
                logger.info("Connected to PostgreSQL for event listening via socket")
                return

            # Parse the database URL for asyncpg connection
            db_url = self.settings.SQLALCHEMY_ASYNC_URI
            if db_url is None:
                raise ValueError("SQLALCHEMY_ASYNC_URI is required for event listening")

            # Remove the +asyncpg part for asyncpg.connect and unhide the password
            connection_url = db_url.render_as_string(False).replace(
                "postgresql+asyncpg://", "postgresql://"
            )

            self.connection = await asyncpg.connect(connection_url)
            logger.info("Connected to PostgreSQL for event listening")

        except Exception as e:
            logger.error(f"Failed to connect to PostgreSQL for event listening: {e}")
            raise

    async def disconnect(self) -> None:
        """Close the PostgreSQL connection."""
        if self.connection:
            await self.connection.close()
            self.connection = None
            logger.info("Disconnected from PostgreSQL event listener")

    def register_handler(
        self, event_type: str, handler: Callable[[FlowEvent], None]
    ) -> None:
        """
        Register an event handler for specific event types.

        Args:
            event_type: Type of event to handle (session_started, node_changed, etc.)
            handler: Async function to call when event occurs
        """
        if event_type not in self.handlers:
            self.handlers[event_type] = []
        self.handlers[event_type].append(handler)
        logger.info(f"Registered handler for event type: {event_type}")

    def unregister_handler(
        self, event_type: str, handler: Callable[[FlowEvent], None]
    ) -> None:
        """Remove an event handler."""
        if event_type in self.handlers:
            try:
                self.handlers[event_type].remove(handler)
                logger.info(f"Unregistered handler for event type: {event_type}")
            except ValueError:
                logger.warning(f"Handler not found for event type: {event_type}")

    async def _handle_notification(
        self, connection: asyncpg.Connection, pid: int, channel: str, payload: str
    ) -> None:
        """
        Handle incoming PostgreSQL notification.

        Args:
            connection: PostgreSQL connection
            pid: Process ID that sent the notification
            channel: Notification channel name
            payload: JSON payload with event data
        """
        try:
            # Parse the event data
            event_data = json.loads(payload)
            try:
                flow_event = FlowEvent.model_validate(event_data)
            except Exception as e:
                logger.error(f"Failed to parse flow event data: {e}")
                return

            logger.info(
                f"Received flow event: {flow_event.event_type} for session {flow_event.session_id}"
            )

            # Dispatch to registered handlers. Build a NEW list — never mutate
            # the stored handler list. `dict.get` returns the live list, so an
            # in-place extend would append the wildcard handlers to
            # self.handlers[event_type] on every notification, growing the list
            # unboundedly and fanning each event out to ever-more handlers.
            handlers = [
                *self.handlers.get(flow_event.event_type, []),
                *self.handlers.get("*", []),
            ]

            for handler in handlers:
                try:
                    if asyncio.iscoroutinefunction(handler):
                        await handler(flow_event)
                    else:
                        handler(flow_event)
                except Exception as e:
                    logger.error(
                        f"Error in event handler for {flow_event.event_type}: {e}"
                    )

        except Exception as e:
            logger.error(f"Failed to process flow event notification: {e}")
            logger.debug(f"Raw payload: {payload}")

    async def start_listening(self) -> None:
        """Start listening for PostgreSQL notifications."""
        if not self.connection:
            await self.connect()

        if self.is_listening:
            logger.warning("Already listening for events")
            return

        try:
            # Listen to the flow_events channel
            await self.connection.add_listener("flow_events", self._handle_notification)
            self.is_listening = True

            logger.info("Started listening for flow events on 'flow_events' channel")

            # Keep the connection alive
            self._listen_task = asyncio.create_task(self._keep_alive())

        except Exception as e:
            logger.error(f"Failed to start listening for events: {e}")
            raise

    async def stop_listening(self) -> None:
        """Stop listening for PostgreSQL notifications."""
        if not self.is_listening:
            return

        try:
            if self.connection:
                await self.connection.remove_listener(
                    "flow_events", self._handle_notification
                )

            if self._listen_task:
                self._listen_task.cancel()
                try:
                    await self._listen_task
                except asyncio.CancelledError:
                    pass
                self._listen_task = None

            self.is_listening = False
            logger.info("Stopped listening for flow events")

        except Exception as e:
            logger.error(f"Error stopping event listener: {e}")

    async def _keep_alive(self) -> None:
        """Keep the connection alive while listening."""
        try:
            while self.is_listening:
                await asyncio.sleep(30)  # Ping every 30 seconds
                if self.connection:
                    await self.connection.execute("SELECT 1")
        except asyncio.CancelledError:
            logger.info("Keep-alive task cancelled")
        except Exception as e:
            logger.error(f"Keep-alive error: {e}")
            self.is_listening = False


# Global event listener instance
_event_listener: Optional[FlowEventListener] = None


def get_event_listener() -> FlowEventListener:
    """Get the global event listener instance."""
    global _event_listener
    if _event_listener is None:
        _event_listener = FlowEventListener()
    # Type assertion to help the typechecker
    return cast(FlowEventListener, _event_listener)


def reset_event_listener() -> None:
    """Reset the global event listener instance for testing.

    This properly closes the connection to prevent connection pool exhaustion.
    Note: Connection closing is best-effort since we may not be in async context.
    """
    global _event_listener
    if _event_listener is not None:
        try:
            # Cancel the keep-alive task if running
            if _event_listener._listen_task is not None:
                _event_listener._listen_task.cancel()
                _event_listener._listen_task = None
            _event_listener.is_listening = False

            # Mark connection for closure - actual close happens on GC
            # We can't reliably close async connections from sync context
            if _event_listener.connection is not None:
                try:
                    if not _event_listener.connection.is_closed():
                        # Try to terminate the connection - this is sync-safe
                        _event_listener.connection.terminate()
                except Exception:
                    # Connection may already be closing/closed
                    pass
                _event_listener.connection = None
        except Exception:
            # Ensure we still reset even if cleanup fails
            pass
    _event_listener = None


# Example event handlers for common use cases


async def log_all_events(event: FlowEvent) -> None:
    """Example handler that logs all flow events."""
    logger.info(
        f"Flow Event: {event.event_type} - Session: {event.session_id} - Node: {event.current_node}"
    )


async def handle_session_completion(event: FlowEvent) -> None:
    """Example handler for session completion events."""
    if event.event_type == "session_status_changed" and event.status == "COMPLETED":
        logger.info(f"Session {event.session_id} completed successfully")
        # Add analytics tracking, cleanup, etc.


async def handle_node_transitions(event: FlowEvent) -> None:
    """Example handler for node transition tracking."""
    if event.event_type == "node_changed":
        logger.info(
            f"Session {event.session_id} moved from {event.previous_node} to {event.current_node}"
        )
        # Add analytics, performance tracking, etc.


# Convenience function to set up common handlers
def register_default_handlers(listener: FlowEventListener) -> None:
    """Register default event handlers for common monitoring."""
    listener.register_handler("*", log_all_events)
    listener.register_handler("session_status_changed", handle_session_completion)
    listener.register_handler("node_changed", handle_node_transitions)

"""
Integration tests for the FlowEventListener service.

These tests verify that the event listener service correctly:
- Connects to PostgreSQL for LISTEN/NOTIFY
- Registers and dispatches event handlers
- Handles different event types
- Cleans up resources properly

Note: These tests create their own isolated listener instances to avoid
interfering with other tests. The main application event listener is
disabled during tests via DISABLE_EVENT_LISTENER=true.
"""

import asyncio
import logging
import uuid
from typing import List

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cms import FlowDefinition
from app.repositories.chat_repository import chat_repo
from app.services.event_listener import (
    FlowEvent,
    FlowEventListener,
    get_event_listener,
    register_default_handlers,
    reset_event_listener,
)

logger = logging.getLogger(__name__)


@pytest.fixture(autouse=True)
async def cleanup_cms_data(async_session):
    """Clean up CMS data before and after each test."""
    cms_tables = [
        "conversation_sessions",
        "conversation_history",
        "conversation_analytics",
        "flow_definitions",
    ]

    await async_session.rollback()

    for table in cms_tables:
        try:
            await async_session.execute(
                text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE")
            )
        except Exception:
            pass
    await async_session.commit()

    yield

    await async_session.rollback()

    for table in cms_tables:
        try:
            await async_session.execute(
                text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE")
            )
        except Exception:
            pass
    await async_session.commit()


@pytest.fixture
async def isolated_event_listener():
    """Create an isolated event listener for testing.

    This creates a fresh listener instance that is separate from
    the global singleton, ensuring test isolation.
    """
    listener = FlowEventListener()

    yield listener

    # Cleanup
    try:
        if listener.is_listening:
            await listener.stop_listening()
        if listener.connection and not listener.connection.is_closed():
            await listener.disconnect()
    except Exception as e:
        logger.warning(f"Error during listener cleanup: {e}")


@pytest.fixture
async def test_flow(async_session: AsyncSession):
    """Create a test flow definition."""
    flow = FlowDefinition(
        id=uuid.uuid4(),
        name="Event Listener Test Flow",
        description="Flow for testing event listener service",
        version="1.0.0",
        flow_data={"nodes": [], "connections": []},
        entry_node_id="start",
        is_published=True,
        is_active=True,
    )

    async_session.add(flow)
    await async_session.commit()
    await async_session.refresh(flow)

    yield flow

    try:
        await async_session.execute(
            text("DELETE FROM conversation_sessions WHERE flow_id = :flow_id"),
            {"flow_id": flow.id},
        )
        await async_session.delete(flow)
        await async_session.commit()
    except Exception as e:
        logger.warning(f"Error during test_flow cleanup: {e}")
        await async_session.rollback()


class TestFlowEventListenerConnection:
    """Test event listener connection management."""

    async def test_connect_creates_asyncpg_connection(
        self, isolated_event_listener: FlowEventListener
    ):
        """Test that connect() establishes an asyncpg connection."""
        assert isolated_event_listener.connection is None

        await isolated_event_listener.connect()

        assert isolated_event_listener.connection is not None
        assert not isolated_event_listener.connection.is_closed()

        # Cleanup
        await isolated_event_listener.disconnect()

    async def test_disconnect_closes_connection(
        self, isolated_event_listener: FlowEventListener
    ):
        """Test that disconnect() properly closes the connection."""
        await isolated_event_listener.connect()
        assert isolated_event_listener.connection is not None

        await isolated_event_listener.disconnect()

        assert isolated_event_listener.connection is None

    async def test_start_listening_begins_listening(
        self, isolated_event_listener: FlowEventListener
    ):
        """Test that start_listening() starts the listener."""
        await isolated_event_listener.start_listening()

        assert isolated_event_listener.is_listening
        assert isolated_event_listener.connection is not None
        assert isolated_event_listener._listen_task is not None

        # Cleanup
        await isolated_event_listener.stop_listening()
        await isolated_event_listener.disconnect()

    async def test_stop_listening_stops_listener(
        self, isolated_event_listener: FlowEventListener
    ):
        """Test that stop_listening() stops the listener."""
        await isolated_event_listener.start_listening()
        assert isolated_event_listener.is_listening

        await isolated_event_listener.stop_listening()

        assert not isolated_event_listener.is_listening
        assert isolated_event_listener._listen_task is None

        # Cleanup
        await isolated_event_listener.disconnect()


class TestFlowEventListenerHandlers:
    """Test event handler registration and dispatch."""

    async def test_register_handler_adds_handler(
        self, isolated_event_listener: FlowEventListener
    ):
        """Test that handlers can be registered for event types."""

        async def test_handler(event: FlowEvent):
            pass

        isolated_event_listener.register_handler("session_started", test_handler)

        assert "session_started" in isolated_event_listener.handlers
        assert test_handler in isolated_event_listener.handlers["session_started"]

    async def test_register_wildcard_handler(
        self, isolated_event_listener: FlowEventListener
    ):
        """Test that wildcard handlers can be registered."""

        async def wildcard_handler(event: FlowEvent):
            pass

        isolated_event_listener.register_handler("*", wildcard_handler)

        assert "*" in isolated_event_listener.handlers
        assert wildcard_handler in isolated_event_listener.handlers["*"]

    async def test_unregister_handler_removes_handler(
        self, isolated_event_listener: FlowEventListener
    ):
        """Test that handlers can be unregistered."""

        async def test_handler(event: FlowEvent):
            pass

        isolated_event_listener.register_handler("session_started", test_handler)
        assert test_handler in isolated_event_listener.handlers["session_started"]

        isolated_event_listener.unregister_handler("session_started", test_handler)
        assert test_handler not in isolated_event_listener.handlers["session_started"]

    async def test_register_default_handlers(
        self, isolated_event_listener: FlowEventListener
    ):
        """Test that default handlers are registered correctly."""
        register_default_handlers(isolated_event_listener)

        # Check wildcard handler for log_all_events
        assert "*" in isolated_event_listener.handlers
        assert len(isolated_event_listener.handlers["*"]) > 0

        # Check event-specific handlers
        assert "session_status_changed" in isolated_event_listener.handlers
        assert "node_changed" in isolated_event_listener.handlers


class TestFlowEventListenerEventDispatch:
    """Test event dispatch to handlers."""

    async def test_handler_receives_events(
        self,
        isolated_event_listener: FlowEventListener,
        async_session: AsyncSession,
        test_flow: FlowDefinition,
    ):
        """Test that registered handlers receive events."""
        received_events: List[FlowEvent] = []

        async def capture_handler(event: FlowEvent):
            received_events.append(event)

        isolated_event_listener.register_handler("session_started", capture_handler)
        await isolated_event_listener.start_listening()

        # Create a session to trigger the event
        session = await chat_repo.create_session(
            async_session,
            flow_id=test_flow.id,
            user_id=None,
            session_token=f"test-{uuid.uuid4().hex[:8]}",
            initial_state={"test": True},
        )

        # Wait for event to be processed
        await asyncio.sleep(0.3)

        # Verify handler received the event
        assert len(received_events) == 1
        assert received_events[0].event_type == "session_started"
        assert received_events[0].session_id == session.id

        # Cleanup
        await isolated_event_listener.stop_listening()
        await isolated_event_listener.disconnect()

    async def test_wildcard_handler_receives_all_events(
        self,
        isolated_event_listener: FlowEventListener,
        async_session: AsyncSession,
        test_flow: FlowDefinition,
    ):
        """Test that wildcard handlers receive all event types."""
        received_events: List[FlowEvent] = []

        async def capture_all_handler(event: FlowEvent):
            received_events.append(event)

        isolated_event_listener.register_handler("*", capture_all_handler)
        await isolated_event_listener.start_listening()

        # Create a session
        session = await chat_repo.create_session(
            async_session,
            flow_id=test_flow.id,
            user_id=None,
            session_token=f"test-{uuid.uuid4().hex[:8]}",
            initial_state={"test": True},
        )

        # Update the session to trigger node_changed
        await chat_repo.update_session_state(
            async_session,
            session_id=session.id,
            state_updates={"updated": True},
            current_node_id="node_2",
            expected_revision=1,
        )

        # Wait for events
        await asyncio.sleep(0.3)

        # Verify multiple events received
        assert len(received_events) >= 2
        event_types = {e.event_type for e in received_events}
        assert "session_started" in event_types
        assert "node_changed" in event_types

        # Cleanup
        await isolated_event_listener.stop_listening()
        await isolated_event_listener.disconnect()

    async def test_handler_error_does_not_crash_listener(
        self,
        isolated_event_listener: FlowEventListener,
        async_session: AsyncSession,
        test_flow: FlowDefinition,
    ):
        """Test that handler errors don't crash the event listener."""
        received_events: List[FlowEvent] = []

        async def failing_handler(event: FlowEvent):
            raise RuntimeError("Simulated handler error")

        async def success_handler(event: FlowEvent):
            received_events.append(event)

        # Register both failing and succeeding handlers
        isolated_event_listener.register_handler("session_started", failing_handler)
        isolated_event_listener.register_handler("session_started", success_handler)
        await isolated_event_listener.start_listening()

        # Create a session to trigger the event
        await chat_repo.create_session(
            async_session,
            flow_id=test_flow.id,
            user_id=None,
            session_token=f"test-{uuid.uuid4().hex[:8]}",
            initial_state={"test": True},
        )

        # Wait for event processing
        await asyncio.sleep(0.3)

        # Verify the success handler still received the event
        assert len(received_events) == 1
        assert isolated_event_listener.is_listening  # Listener still running

        # Cleanup
        await isolated_event_listener.stop_listening()
        await isolated_event_listener.disconnect()


class TestFlowEventListenerGlobalSingleton:
    """Test the global singleton pattern."""

    def test_get_event_listener_returns_singleton(self):
        """Test that get_event_listener returns the same instance."""
        # Reset first to start fresh
        reset_event_listener()

        listener1 = get_event_listener()
        listener2 = get_event_listener()

        assert listener1 is listener2

        # Cleanup
        reset_event_listener()

    def test_reset_event_listener_clears_singleton(self):
        """Test that reset_event_listener clears the singleton."""
        listener1 = get_event_listener()
        reset_event_listener()
        listener2 = get_event_listener()

        assert listener1 is not listener2

        # Cleanup
        reset_event_listener()


class TestFlowEventModel:
    """Test the FlowEvent Pydantic model."""

    def test_flow_event_parsing(self):
        """Test that FlowEvent correctly parses event data."""
        event_data = {
            "event_type": "session_started",
            "session_id": str(uuid.uuid4()),
            "flow_id": str(uuid.uuid4()),
            "timestamp": 1234567890.123,
            "user_id": str(uuid.uuid4()),
            "current_node": "start",
            "status": "ACTIVE",
            "revision": 1,
        }

        event = FlowEvent.model_validate(event_data)

        assert event.event_type == "session_started"
        assert event.current_node == "start"
        assert event.status == "ACTIVE"
        assert event.revision == 1

    def test_flow_event_optional_fields(self):
        """Test that optional fields work correctly."""
        event_data = {
            "event_type": "session_started",
            "session_id": str(uuid.uuid4()),
            "flow_id": str(uuid.uuid4()),
            "timestamp": 1234567890.123,
        }

        event = FlowEvent.model_validate(event_data)

        assert event.user_id is None
        assert event.current_node is None
        assert event.previous_node is None

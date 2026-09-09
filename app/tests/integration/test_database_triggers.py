"""
Integration tests for PostgreSQL database triggers.

This module tests the notify_flow_event() trigger that emits PostgreSQL NOTIFY events
when conversation_sessions table changes occur. Tests verify that proper NOTIFY
payloads are sent for INSERT, UPDATE, and DELETE operations.
"""

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List

import asyncpg
import pytest
from sqlalchemy import text

from app.config import get_settings


@pytest.fixture(autouse=True)
async def cleanup_cms_data(async_session):
    """Clean up CMS data before and after each test to ensure test isolation."""
    cms_tables = [
        "cms_content",
        "cms_content_variants",
        "flow_definitions",
        "flow_nodes",
        "flow_connections",
        "conversation_sessions",
        "conversation_history",
        "conversation_analytics",
    ]

    await async_session.rollback()

    # Clean up before test runs
    for table in cms_tables:
        try:
            await async_session.execute(
                text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE")
            )
        except Exception:
            # Table might not exist, skip it
            pass
    await async_session.commit()

    yield

    await async_session.rollback()

    # Clean up after test runs
    for table in cms_tables:
        try:
            await async_session.execute(
                text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE")
            )
        except Exception:
            # Table might not exist, skip it
            pass
    await async_session.commit()


from sqlalchemy.ext.asyncio import AsyncSession

from app import crud
from app.models.cms import FlowDefinition, SessionStatus
from app.repositories.chat_repository import chat_repo
from app.schemas.users.user_create import UserCreateIn

logger = logging.getLogger(__name__)


@pytest.fixture
async def notify_listener(reset_global_state):
    """Set up PostgreSQL LISTEN connection for testing NOTIFY events.

    Depends on reset_global_state to ensure global event listener connections
    are cleaned up before creating this additional listener connection.
    """
    received_events: List[Dict[str, Any]] = []
    conn = None

    def listener_callback(connection, pid, channel, payload):
        """Callback to capture NOTIFY events."""
        try:
            event_data = json.loads(payload)
            received_events.append(event_data)
            logger.debug(f"Received NOTIFY event: {event_data}")
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse NOTIFY payload: {e}")

    # Connect directly with asyncpg for LISTEN/NOTIFY using environment variables
    db_host = os.getenv("POSTGRESQL_SERVER", "localhost").rstrip("/")
    db_password = os.getenv("POSTGRESQL_PASSWORD", "password")
    db_name = get_settings().POSTGRESQL_DATABASE
    db_port = 5432

    try:
        logger.debug(f"Connecting to database at {db_host}:{db_port}/{db_name}")
        conn = await asyncio.wait_for(
            asyncpg.connect(
                host=db_host,
                port=db_port,
                user="postgres",
                password=db_password,
                database=db_name,
            ),
            timeout=10.0,
        )
        await conn.add_listener("flow_events", listener_callback)

        yield conn, received_events

    finally:
        if conn is not None:
            try:
                await conn.remove_listener("flow_events", listener_callback)
            except Exception:
                pass
            try:
                if not conn.is_closed():
                    conn.terminate()
            except Exception:
                pass


@pytest.fixture
async def test_flow(async_session: AsyncSession):
    """Create a test flow definition for trigger tests."""
    flow = FlowDefinition(
        id=uuid.uuid4(),
        name="Test Flow for Trigger Tests",
        description="A flow used for testing database triggers",
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

    # Cleanup - first remove any sessions that reference this flow
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


@pytest.fixture
async def test_user(async_session: AsyncSession):
    """Create a test user for trigger tests."""
    user = await crud.user.acreate(
        db=async_session,
        obj_in=UserCreateIn(
            name="Trigger Test User",
            email=f"trigger-test-{uuid.uuid4().hex[:8]}@test.com",
            first_name="Trigger",
            last_name_initial="T",
        ),
    )

    yield user

    # Cleanup
    try:
        # First remove any sessions that reference this user
        await async_session.execute(
            text("DELETE FROM conversation_sessions WHERE user_id = :user_id"),
            {"user_id": user.id},
        )
        await crud.user.aremove(db=async_session, id=user.id)
    except Exception as e:
        logger.warning(f"Error during test_user cleanup: {e}")
        await async_session.rollback()


class TestNotifyFlowEventTrigger:
    """Test cases for the notify_flow_event() PostgreSQL trigger."""

    async def test_session_started_trigger_notification(
        self,
        async_session: AsyncSession,
        notify_listener,
        test_flow: FlowDefinition,
        test_user,
    ):
        """Test that creating a conversation session triggers session_started notification."""
        conn, received_events = notify_listener

        # Clear any existing events
        received_events.clear()

        # Create a session through the chat_repo
        session_token = f"test-token-{uuid.uuid4().hex[:8]}"
        session = await chat_repo.create_session(
            async_session,
            flow_id=test_flow.id,
            user_id=test_user.id,
            session_token=session_token,
            initial_state={"test": "data", "counter": 1},
        )

        # Wait for notification delivery
        await asyncio.sleep(0.2)

        # Verify notification was received
        assert (
            len(received_events) == 1
        ), f"Expected 1 event, got {len(received_events)}"

        event = received_events[0]
        assert event["event_type"] == "session_started"
        assert event["session_id"] == str(session.id)
        assert event["flow_id"] == str(test_flow.id)
        assert event["user_id"] == str(test_user.id)
        assert event["status"] == "ACTIVE"
        assert event["revision"] == 1
        assert "timestamp" in event

        # Verify timestamp is reasonable (within last minute)
        event_time = datetime.fromtimestamp(event["timestamp"])
        time_diff = datetime.utcnow() - event_time
        assert time_diff < timedelta(minutes=1)

    async def test_node_changed_trigger_notification(
        self,
        async_session: AsyncSession,
        notify_listener,
        test_flow: FlowDefinition,
        test_user,
    ):
        """Test that updating current_node_id triggers node_changed notification."""
        conn, received_events = notify_listener

        # Create initial session
        session_token = f"test-token-{uuid.uuid4().hex[:8]}"
        session = await chat_repo.create_session(
            async_session,
            flow_id=test_flow.id,
            user_id=test_user.id,
            session_token=session_token,
            initial_state={"step": "initial"},
        )

        # Clear events from session creation
        received_events.clear()

        # Update the current node
        updated_session = await chat_repo.update_session_state(
            async_session,
            session_id=session.id,
            state_updates={"step": "updated"},
            current_node_id="node_2",
            expected_revision=1,
        )

        # Wait for notification
        await asyncio.sleep(0.2)

        # Verify notification was received
        assert len(received_events) == 1

        event = received_events[0]
        assert event["event_type"] == "node_changed"
        assert event["session_id"] == str(session.id)
        assert event["flow_id"] == str(test_flow.id)
        assert event["user_id"] == str(test_user.id)
        assert event["current_node"] == "node_2"
        assert event["previous_node"] is None  # Was None initially
        assert event["revision"] == 2
        assert event["previous_revision"] == 1

    async def test_session_status_changed_trigger_notification(
        self,
        async_session: AsyncSession,
        notify_listener,
        test_flow: FlowDefinition,
        test_user,
    ):
        """Test that updating session status triggers session_status_changed notification."""
        conn, received_events = notify_listener

        # Create initial session
        session_token = f"test-token-{uuid.uuid4().hex[:8]}"
        session = await chat_repo.create_session(
            async_session,
            flow_id=test_flow.id,
            user_id=test_user.id,
            session_token=session_token,
            initial_state={"progress": "starting"},
        )

        # Clear events from session creation
        received_events.clear()

        # End the session
        ended_session = await chat_repo.end_session(
            async_session, session_id=session.id, status=SessionStatus.COMPLETED
        )

        # Wait for notification
        await asyncio.sleep(0.2)

        # Verify notification was received
        assert len(received_events) == 1

        event = received_events[0]
        assert event["event_type"] == "session_status_changed"
        assert event["session_id"] == str(session.id)
        assert event["status"] == "COMPLETED"
        assert event["previous_status"] == "ACTIVE"

    async def test_session_updated_trigger_notification(
        self,
        async_session: AsyncSession,
        notify_listener,
        test_flow: FlowDefinition,
        test_user,
    ):
        """Test that updating revision triggers session_updated notification."""
        conn, received_events = notify_listener

        # Create initial session
        session_token = f"test-token-{uuid.uuid4().hex[:8]}"
        session = await chat_repo.create_session(
            async_session,
            flow_id=test_flow.id,
            user_id=test_user.id,
            session_token=session_token,
            initial_state={"data": "initial"},
        )

        # Clear events from session creation
        received_events.clear()

        # Update state without changing node or status (only revision changes)
        updated_session = await chat_repo.update_session_state(
            async_session,
            session_id=session.id,
            state_updates={"data": "updated", "new_field": "value"},
            expected_revision=1,
        )

        # Wait for notification
        await asyncio.sleep(0.2)

        # Verify notification was received
        assert len(received_events) == 1

        event = received_events[0]
        assert event["event_type"] == "session_updated"
        assert event["session_id"] == str(session.id)
        assert event["revision"] == 2
        assert event["previous_revision"] == 1

    async def test_session_deleted_trigger_notification(
        self,
        async_session: AsyncSession,
        notify_listener,
        test_flow: FlowDefinition,
        test_user,
    ):
        """Test that deleting a session triggers session_deleted notification."""
        conn, received_events = notify_listener

        # Create initial session
        session_token = f"test-token-{uuid.uuid4().hex[:8]}"
        session = await chat_repo.create_session(
            async_session,
            flow_id=test_flow.id,
            user_id=test_user.id,
            session_token=session_token,
            initial_state={"to_be": "deleted"},
        )

        # Clear events from session creation
        received_events.clear()

        # Delete the session directly via SQL
        await async_session.execute(
            text("DELETE FROM conversation_sessions WHERE id = :session_id"),
            {"session_id": session.id},
        )
        await async_session.commit()

        # Wait for notification
        await asyncio.sleep(0.2)

        # Verify notification was received
        assert len(received_events) == 1

        event = received_events[0]
        assert event["event_type"] == "session_deleted"
        assert event["session_id"] == str(session.id)
        assert event["flow_id"] == str(test_flow.id)
        assert event["user_id"] == str(test_user.id)
        assert "timestamp" in event

    async def test_no_unnecessary_notifications(
        self,
        async_session: AsyncSession,
        notify_listener,
        test_flow: FlowDefinition,
        test_user,
    ):
        """Test that updating non-tracked fields doesn't trigger notifications."""
        conn, received_events = notify_listener

        # Create initial session
        session_token = f"test-token-{uuid.uuid4().hex[:8]}"
        session = await chat_repo.create_session(
            async_session,
            flow_id=test_flow.id,
            user_id=test_user.id,
            session_token=session_token,
            initial_state={"data": "initial"},
        )

        # Clear events from session creation
        received_events.clear()

        # Update only last_activity_at (should not trigger notification since other fields unchanged)
        await async_session.execute(
            text("""
                UPDATE conversation_sessions 
                SET last_activity_at = :new_time 
                WHERE id = :session_id
            """),
            {"session_id": session.id, "new_time": datetime.utcnow()},
        )
        await async_session.commit()

        # Wait for potential notification
        await asyncio.sleep(0.2)

        # Verify no notification was sent
        assert len(received_events) == 0, f"Expected no events, got {received_events}"

    async def test_multiple_simultaneous_triggers(
        self,
        async_session: AsyncSession,
        notify_listener,
        test_flow: FlowDefinition,
        test_user,
    ):
        """Test that multiple simultaneous trigger events are all captured."""
        conn, received_events = notify_listener

        # Clear any existing events
        received_events.clear()

        # Create multiple sessions simultaneously
        session_tokens = [f"test-token-{i}-{uuid.uuid4().hex[:8]}" for i in range(3)]

        sessions = []
        for i, token in enumerate(session_tokens):
            session = await chat_repo.create_session(
                async_session,
                flow_id=test_flow.id,
                user_id=test_user.id,
                session_token=token,
                initial_state={"session_number": i},
            )
            sessions.append(session)

        # Wait for all notifications
        await asyncio.sleep(0.3)

        # Verify all notifications were received
        assert len(received_events) == 3

        # Verify all are session_started events
        for event in received_events:
            assert event["event_type"] == "session_started"
            assert event["flow_id"] == str(test_flow.id)
            assert event["user_id"] == str(test_user.id)

        # Verify all session IDs are unique and match our created sessions
        event_session_ids = {event["session_id"] for event in received_events}
        created_session_ids = {str(session.id) for session in sessions}
        assert event_session_ids == created_session_ids

    async def test_trigger_with_null_user_id(
        self, async_session: AsyncSession, notify_listener, test_flow: FlowDefinition
    ):
        """Test that trigger works correctly with NULL user_id (anonymous sessions)."""
        conn, received_events = notify_listener

        # Clear any existing events
        received_events.clear()

        # Create session with NULL user_id
        session_token = f"test-anonymous-{uuid.uuid4().hex[:8]}"
        session = await chat_repo.create_session(
            async_session,
            flow_id=test_flow.id,
            user_id=None,  # Anonymous session
            session_token=session_token,
            initial_state={"anonymous": True},
        )

        # Wait for notification
        await asyncio.sleep(0.2)

        # Verify notification was received
        assert len(received_events) == 1

        event = received_events[0]
        assert event["event_type"] == "session_started"
        assert event["session_id"] == str(session.id)
        assert event["flow_id"] == str(test_flow.id)
        assert event["user_id"] is None  # Should be null in JSON

    async def test_trigger_payload_json_structure(
        self,
        async_session: AsyncSession,
        notify_listener,
        test_flow: FlowDefinition,
        test_user,
    ):
        """Test that trigger payload is valid JSON with expected structure."""
        conn, received_events = notify_listener

        # Clear any existing events
        received_events.clear()

        # Create session to test payload structure
        session_token = f"test-payload-{uuid.uuid4().hex[:8]}"
        session = await chat_repo.create_session(
            async_session,
            flow_id=test_flow.id,
            user_id=test_user.id,
            session_token=session_token,
            initial_state={"test": "payload"},
        )

        # Wait for notification
        await asyncio.sleep(0.2)

        # Verify notification structure
        assert len(received_events) == 1
        event = received_events[0]

        # Verify all required fields are present
        required_fields = [
            "event_type",
            "session_id",
            "flow_id",
            "user_id",
            "current_node",
            "status",
            "revision",
            "timestamp",
        ]

        for field in required_fields:
            assert field in event, f"Missing required field: {field}"

        # Verify field types
        assert isinstance(event["event_type"], str)
        assert isinstance(event["session_id"], str)
        assert isinstance(event["flow_id"], str)
        assert isinstance(event["user_id"], str)
        assert isinstance(event["revision"], int)
        assert isinstance(event["timestamp"], (int, float))

        # Verify UUIDs are valid format
        uuid.UUID(event["session_id"])  # Should not raise exception
        uuid.UUID(event["flow_id"])  # Should not raise exception
        uuid.UUID(event["user_id"])  # Should not raise exception

    async def test_trigger_performance_with_batch_operations(
        self,
        async_session: AsyncSession,
        notify_listener,
        test_flow: FlowDefinition,
        test_user,
    ):
        """Test trigger performance with batch operations."""
        conn, received_events = notify_listener

        # Clear any existing events
        received_events.clear()

        # Create a batch of sessions
        batch_size = 10
        session_tokens = [
            f"batch-{i}-{uuid.uuid4().hex[:6]}" for i in range(batch_size)
        ]

        start_time = asyncio.get_event_loop().time()

        # Create sessions in batch
        for i, token in enumerate(session_tokens):
            await chat_repo.create_session(
                async_session,
                flow_id=test_flow.id,
                user_id=test_user.id,
                session_token=token,
                initial_state={"batch_index": i},
            )

        end_time = asyncio.get_event_loop().time()
        creation_time = end_time - start_time

        # Wait for all notifications
        await asyncio.sleep(0.5)

        # Verify all notifications received
        assert len(received_events) == batch_size

        # Verify performance is reasonable (should be fast)
        assert creation_time < 5.0, f"Batch creation took too long: {creation_time}s"

        # Verify all events are session_started
        for event in received_events:
            assert event["event_type"] == "session_started"
            assert event["flow_id"] == str(test_flow.id)
            assert event["user_id"] == str(test_user.id)

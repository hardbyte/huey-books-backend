"""Integration tests for async task idempotency implementation."""

import asyncio
import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select, text


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


from app.db.session import get_async_session_maker
from app.models.cms import (
    ConversationSession,
    FlowDefinition,
    IdempotencyRecord,
    SessionStatus,
    TaskExecutionStatus,
)
from app.repositories.chat_repository import chat_repo
from app.tests.util.random_strings import random_lower_string


@pytest.mark.asyncio
async def test_get_session_by_id(async_session, test_user_account):
    """Test getting session by ID."""
    flow = FlowDefinition(
        name="test_flow",
        description="Test flow",
        version="1.0",
        flow_data={"nodes": [], "connections": []},
        entry_node_id="start_node",
    )
    async_session.add(flow)
    await async_session.commit()

    session = ConversationSession(
        flow_id=flow.id,
        session_token=f"test_token_{random_lower_string(10)}",
        state={"test": "data"},
        info={},
        status=SessionStatus.ACTIVE,
        revision=1,
        state_hash="test_hash",
    )
    async_session.add(session)
    await async_session.commit()

    retrieved_session = await chat_repo.get_session_by_id(async_session, session.id)

    assert retrieved_session is not None
    assert retrieved_session.id == session.id
    assert retrieved_session.session_token == session.session_token
    assert retrieved_session.state == {"test": "data"}


@pytest.mark.asyncio
async def test_get_session_by_id_not_found(async_session):
    """Test getting non-existent session by ID."""
    non_existent_id = uuid.uuid4()
    retrieved_session = await chat_repo.get_session_by_id(
        async_session, non_existent_id
    )

    assert retrieved_session is None


@pytest.mark.asyncio
async def test_acquire_idempotency_lock_first_time(async_session):
    """Test acquiring idempotency lock for the first time."""
    session_id = uuid.uuid4()
    idempotency_key = f"test_session_{random_lower_string(10)}:test_node:1"

    acquired, result_data = await chat_repo.acquire_idempotency_lock(
        async_session,
        idempotency_key=idempotency_key,
        session_id=session_id,
        node_id="test_node",
        session_revision=1,
    )

    assert acquired is True
    assert result_data is None

    # Verify record was created
    result = await async_session.scalars(
        select(IdempotencyRecord).where(
            IdempotencyRecord.idempotency_key == idempotency_key
        )
    )
    record = result.first()

    assert record is not None
    assert record.status == TaskExecutionStatus.PROCESSING
    assert record.session_id == session_id
    assert record.node_id == "test_node"
    assert record.session_revision == 1


@pytest.mark.asyncio
async def test_acquire_idempotency_lock_duplicate(async_session):
    """Test acquiring idempotency lock when already exists."""
    session_id = uuid.uuid4()
    idempotency_key = f"test_session_{random_lower_string(10)}:test_node:1"

    # Create existing record
    existing_record = IdempotencyRecord(
        idempotency_key=idempotency_key,
        status=TaskExecutionStatus.COMPLETED,
        session_id=session_id,
        node_id="test_node",
        session_revision=1,
        result_data={"status": "already_processed"},
        completed_at=datetime.utcnow(),
    )
    async_session.add(existing_record)
    await async_session.commit()

    acquired, result_data = await chat_repo.acquire_idempotency_lock(
        async_session,
        idempotency_key=idempotency_key,
        session_id=session_id,
        node_id="test_node",
        session_revision=1,
    )

    assert acquired is False
    assert result_data is not None
    assert result_data["status"] == "completed"
    assert result_data["result_data"] == {"status": "already_processed"}
    assert result_data["idempotency_key"] == idempotency_key


@pytest.mark.asyncio
async def test_complete_idempotency_record_success(async_session):
    """Test completing idempotency record successfully."""
    session_id = uuid.uuid4()
    idempotency_key = f"test_session_{random_lower_string(10)}:test_node:1"

    # Create processing record
    record = IdempotencyRecord(
        idempotency_key=idempotency_key,
        status=TaskExecutionStatus.PROCESSING,
        session_id=session_id,
        node_id="test_node",
        session_revision=1,
    )
    async_session.add(record)
    await async_session.commit()

    result_data = {"status": "completed", "action_type": "set_variable"}

    await chat_repo.complete_idempotency_record(
        async_session,
        idempotency_key=idempotency_key,
        success=True,
        result_data=result_data,
    )

    # Verify record was updated
    await async_session.refresh(record)
    assert record.status == TaskExecutionStatus.COMPLETED
    assert record.result_data == result_data
    assert record.error_message is None
    assert record.completed_at is not None


@pytest.mark.asyncio
async def test_complete_idempotency_record_failure(async_session):
    """Test completing idempotency record with failure."""
    session_id = uuid.uuid4()
    idempotency_key = f"test_session_{random_lower_string(10)}:test_node:1"

    # Create processing record
    record = IdempotencyRecord(
        idempotency_key=idempotency_key,
        status=TaskExecutionStatus.PROCESSING,
        session_id=session_id,
        node_id="test_node",
        session_revision=1,
    )
    async_session.add(record)
    await async_session.commit()

    error_message = "Database connection failed"

    await chat_repo.complete_idempotency_record(
        async_session,
        idempotency_key=idempotency_key,
        success=False,
        error_message=error_message,
    )

    # Verify record was updated
    await async_session.refresh(record)
    assert record.status == TaskExecutionStatus.FAILED
    assert record.result_data is None
    assert record.error_message == error_message
    assert record.completed_at is not None


@pytest.mark.asyncio
async def test_action_node_task_success(internal_async_client, async_session):
    """Test successful action node task processing."""
    # Create test session
    flow = FlowDefinition(
        name="test_flow",
        description="Test flow",
        version="1.0",
        flow_data={"nodes": [], "connections": []},
        entry_node_id="start_node",
    )
    async_session.add(flow)
    await async_session.commit()

    session = ConversationSession(
        flow_id=flow.id,
        session_token=f"test_token_{random_lower_string(10)}",
        state={"test": "data"},
        info={},
        status=SessionStatus.ACTIVE,
        revision=1,
        state_hash="test_hash",
    )
    async_session.add(session)
    await async_session.commit()

    payload = {
        "task_type": "action_node",
        "session_id": str(session.id),
        "node_id": "test_node",
        "session_revision": 1,
        "idempotency_key": f"{session.id}:test_node:1",
        "action_type": "set_variable",
        "params": {"variable": "test_var", "value": "test_value"},
    }

    response = await internal_async_client.post(
        "/v1/internal/tasks/action-node",
        json=payload,
        headers={"X-Idempotency-Key": payload["idempotency_key"]},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert data["idempotency_key"] == payload["idempotency_key"]
    assert data["action_type"] == "set_variable"

    # Verify idempotency record was created
    result = await async_session.scalars(
        select(IdempotencyRecord).where(
            IdempotencyRecord.idempotency_key == payload["idempotency_key"]
        )
    )
    record = result.first()
    assert record is not None
    assert record.status == TaskExecutionStatus.COMPLETED


@pytest.mark.asyncio
async def test_action_node_task_duplicate(internal_async_client, async_session):
    """Test duplicate action node task returns cached result."""
    session_id = uuid.uuid4()
    idempotency_key = f"{session_id}:test_node:1"

    # Create existing completed record
    existing_record = IdempotencyRecord(
        idempotency_key=idempotency_key,
        status=TaskExecutionStatus.COMPLETED,
        session_id=session_id,
        node_id="test_node",
        session_revision=1,
        result_data={"status": "completed", "action_type": "set_variable"},
        completed_at=datetime.utcnow(),
    )
    async_session.add(existing_record)
    await async_session.commit()

    payload = {
        "task_type": "action_node",
        "session_id": str(session_id),
        "node_id": "test_node",
        "session_revision": 1,
        "idempotency_key": idempotency_key,
        "action_type": "set_variable",
        "params": {"variable": "test_var", "value": "test_value"},
    }

    response = await internal_async_client.post(
        "/v1/internal/tasks/action-node",
        json=payload,
        headers={"X-Idempotency-Key": idempotency_key},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert data["result_data"]["status"] == "completed"
    assert data["result_data"]["action_type"] == "set_variable"


@pytest.mark.asyncio
async def test_action_node_task_session_not_found(internal_async_client):
    """Test action node task with non-existent session returns 200 OK."""
    non_existent_session_id = uuid.uuid4()
    idempotency_key = f"{non_existent_session_id}:test_node:1"

    payload = {
        "task_type": "action_node",
        "session_id": str(non_existent_session_id),
        "node_id": "test_node",
        "session_revision": 1,
        "idempotency_key": idempotency_key,
        "action_type": "set_variable",
        "params": {"variable": "test_var", "value": "test_value"},
    }

    response = await internal_async_client.post(
        "/v1/internal/tasks/action-node",
        json=payload,
        headers={"X-Idempotency-Key": idempotency_key},
    )

    # Should return 200 OK (not 404) to prevent Cloud Tasks retries
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "discarded_session_not_found"
    assert data["idempotency_key"] == idempotency_key


@pytest.mark.asyncio
async def test_action_node_task_stale_revision(internal_async_client, async_session):
    """Test action node task with stale revision returns 200 OK."""
    # Create test session with higher revision
    flow = FlowDefinition(
        name="test_flow",
        description="Test flow",
        version="1.0",
        flow_data={"nodes": [], "connections": []},
        entry_node_id="start_node",
    )
    async_session.add(flow)
    await async_session.commit()

    session = ConversationSession(
        flow_id=flow.id,
        session_token=f"test_token_{random_lower_string(10)}",
        state={"test": "data"},
        info={},
        status=SessionStatus.ACTIVE,
        revision=3,  # Higher revision
        state_hash="test_hash",
    )
    async_session.add(session)
    await async_session.commit()

    idempotency_key = f"{session.id}:test_node:1"
    payload = {
        "task_type": "action_node",
        "session_id": str(session.id),
        "node_id": "test_node",
        "session_revision": 1,  # Stale revision
        "idempotency_key": idempotency_key,
        "action_type": "set_variable",
        "params": {"variable": "test_var", "value": "test_value"},
    }

    response = await internal_async_client.post(
        "/v1/internal/tasks/action-node",
        json=payload,
        headers={"X-Idempotency-Key": idempotency_key},
    )

    # Should return 200 OK (not error) to prevent Cloud Tasks retries
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "discarded_stale"
    assert data["idempotency_key"] == idempotency_key


@pytest.mark.asyncio
async def test_webhook_node_task_success(internal_async_client, async_session):
    """Test successful webhook node task processing."""
    # Create test session
    flow = FlowDefinition(
        name="test_flow",
        description="Test flow",
        version="1.0",
        flow_data={"nodes": [], "connections": []},
        entry_node_id="start_node",
        info={},
    )
    async_session.add(flow)
    await async_session.commit()

    session = ConversationSession(
        flow_id=flow.id,
        session_token=f"test_token_{random_lower_string(10)}",
        state={"test": "data"},
        info={},
        status=SessionStatus.ACTIVE,
        revision=1,
        state_hash="test_hash",
    )
    async_session.add(session)
    await async_session.commit()

    idempotency_key = f"{session.id}:webhook_node:1"
    payload = {
        "task_type": "webhook_node",
        "session_id": str(session.id),
        "node_id": "webhook_node",
        "session_revision": 1,
        "idempotency_key": idempotency_key,
        "webhook_config": {
            "url": "https://httpbin.org/post",
            "method": "POST",
            "headers": {"Content-Type": "application/json"},
            "payload": {"test": "data"},
        },
    }

    from unittest.mock import MagicMock

    # Set up the mock properly for async httpx
    with patch("httpx.AsyncClient") as mock_client:
        mock_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"success": True}
        mock_response.raise_for_status.return_value = None

        mock_instance.request = AsyncMock(return_value=mock_response)
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=None)

        mock_client.return_value = mock_instance

        response = await internal_async_client.post(
            "/v1/internal/tasks/webhook-node",
            json=payload,
            headers={"X-Idempotency-Key": idempotency_key},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert data["idempotency_key"] == idempotency_key
    assert data["webhook_result"]["webhook_executed"] is True

    # Verify idempotency record was created
    result = await async_session.scalars(
        select(IdempotencyRecord).where(
            IdempotencyRecord.idempotency_key == idempotency_key
        )
    )
    record = result.first()
    assert record is not None
    assert record.status == TaskExecutionStatus.COMPLETED


@pytest.mark.asyncio
async def test_concurrent_task_processing():
    """Test that concurrent tasks with same idempotency key are handled correctly."""
    session_id = uuid.uuid4()
    idempotency_key = f"{session_id}:{random_lower_string(10)}:test_node:1"

    async def try_acquire_lock():
        # Create a separate session for each concurrent operation
        # This simulates how it works in production where each request gets its own session
        session_factory = get_async_session_maker()
        session = session_factory()
        try:
            # Add timeout to prevent hanging while still allowing race condition detection
            return await asyncio.wait_for(
                chat_repo.acquire_idempotency_lock(
                    session,
                    idempotency_key=idempotency_key,
                    session_id=session_id,
                    node_id="test_node",
                    session_revision=1,
                ),
                timeout=5.0,  # 5 second timeout per operation
            )
        except asyncio.TimeoutError:
            return (False, "timeout")
        finally:
            await session.close()

    # Simulate concurrent requests with overall timeout
    try:
        results = await asyncio.wait_for(
            asyncio.gather(
                try_acquire_lock(),
                try_acquire_lock(),
                try_acquire_lock(),
                return_exceptions=True,
            ),
            timeout=15.0,  # 15 second overall timeout for all concurrent operations
        )
    except asyncio.TimeoutError:
        pytest.fail("Concurrent task processing test timed out after 15 seconds")

    # Only one should succeed in acquiring the lock
    successful_acquisitions = [
        r for r in results if isinstance(r, tuple) and r[0] is True
    ]
    failed_acquisitions = [r for r in results if isinstance(r, tuple) and r[0] is False]
    timeout_failures = [
        r for r in results if isinstance(r, tuple) and r[1] == "timeout"
    ]
    exceptions = [r for r in results if isinstance(r, Exception)]

    # Log results for debugging
    print("Concurrent lock test results:")
    print(f"  Successful acquisitions: {len(successful_acquisitions)}")
    print(f"  Failed acquisitions: {len(failed_acquisitions)}")
    print(f"  Timeout failures: {len(timeout_failures)}")
    print(f"  Exceptions: {len(exceptions)}")

    # In case of exceptions, log them for debugging
    if exceptions:
        for exc in exceptions:
            print(f"Exception during concurrent processing: {exc}")

    # Exactly one should succeed, others should fail (including timeouts)
    assert len(successful_acquisitions) == 1, (
        f"Expected 1 successful acquisition, got {len(successful_acquisitions)}"
    )
    assert len(failed_acquisitions) + len(timeout_failures) >= 2, (
        "Expected at least 2 failures (normal or timeout)"
    )

    # Verify only one record was created using a separate session
    verification_session = get_async_session_maker()()
    try:
        result = await verification_session.scalars(
            select(IdempotencyRecord).where(
                IdempotencyRecord.idempotency_key == idempotency_key
            )
        )
        records = result.all()
        assert len(records) == 1
        assert records[0].status == TaskExecutionStatus.PROCESSING
    finally:
        await verification_session.close()


@pytest.mark.asyncio
async def test_expired_records_query(async_session):
    """Test finding expired idempotency records."""
    # Clean up any existing expired records from previous tests
    from sqlalchemy import delete, func

    await async_session.execute(
        delete(IdempotencyRecord).where(
            IdempotencyRecord.expires_at < func.current_timestamp()
        )
    )
    await async_session.commit()

    # Create expired record
    expired_record = IdempotencyRecord(
        idempotency_key=f"expired_key_{random_lower_string(10)}",
        status=TaskExecutionStatus.COMPLETED,
        session_id=uuid.uuid4(),
        node_id="test_node",
        session_revision=1,
        expires_at=datetime.utcnow() - timedelta(hours=1),  # Expired
    )

    # Create current record
    current_record = IdempotencyRecord(
        idempotency_key=f"current_key_{random_lower_string(10)}",
        status=TaskExecutionStatus.PROCESSING,
        session_id=uuid.uuid4(),
        node_id="test_node",
        session_revision=1,
        expires_at=datetime.utcnow() + timedelta(hours=1),  # Not expired
    )

    async_session.add_all([expired_record, current_record])
    await async_session.commit()

    try:
        # Query for expired records
        from sqlalchemy import func

        result = await async_session.scalars(
            select(IdempotencyRecord).where(
                IdempotencyRecord.expires_at < func.current_timestamp()
            )
        )
        expired_records = result.all()

        assert len(expired_records) == 1
        assert expired_records[0].idempotency_key.startswith("expired_key_")
    finally:
        # Clean up test data
        await async_session.delete(expired_record)
        await async_session.delete(current_record)
        await async_session.commit()


@pytest.mark.asyncio
async def test_stuck_processing_tasks_query(async_session):
    """Test finding tasks stuck in processing state."""
    # Clean up any existing stuck records from previous tests
    from sqlalchemy import delete, func

    await async_session.execute(
        delete(IdempotencyRecord).where(
            IdempotencyRecord.status == TaskExecutionStatus.PROCESSING,
            IdempotencyRecord.created_at
            < func.current_timestamp() - timedelta(minutes=5),
        )
    )
    await async_session.commit()

    # Create stuck task (processing for more than 5 minutes)
    stuck_record = IdempotencyRecord(
        idempotency_key=f"stuck_key_{random_lower_string(10)}",
        status=TaskExecutionStatus.PROCESSING,
        session_id=uuid.uuid4(),
        node_id="test_node",
        session_revision=1,
        created_at=datetime.utcnow() - timedelta(minutes=10),  # Old
    )

    # Create recent processing task
    recent_record = IdempotencyRecord(
        idempotency_key=f"recent_key_{random_lower_string(10)}",
        status=TaskExecutionStatus.PROCESSING,
        session_id=uuid.uuid4(),
        node_id="test_node",
        session_revision=1,
        created_at=datetime.utcnow() - timedelta(minutes=1),  # Recent
    )

    async_session.add_all([stuck_record, recent_record])
    await async_session.commit()

    try:
        # Query for stuck tasks
        from sqlalchemy import func

        result = await async_session.scalars(
            select(IdempotencyRecord).where(
                IdempotencyRecord.status == TaskExecutionStatus.PROCESSING,
                IdempotencyRecord.created_at
                < func.current_timestamp() - timedelta(minutes=5),
            )
        )
        stuck_records = result.all()

        assert len(stuck_records) == 1
        assert stuck_records[0].idempotency_key.startswith("stuck_key_")
    finally:
        # Clean up test data
        await async_session.delete(stuck_record)
        await async_session.delete(recent_record)
        await async_session.commit()

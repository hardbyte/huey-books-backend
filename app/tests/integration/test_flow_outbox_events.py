"""
Integration tests to verify EventOutbox entries are created for flow operations.

Covers:
- flow_created on POST /v1/cms/flows
- flow_updated on PUT /v1/cms/flows/{id}
- flow_published on PUT /v1/cms/flows/{id} with publish=true
"""

import pytest
from sqlalchemy import text
from starlette import status


@pytest.fixture(autouse=True)
async def cleanup_event_outbox(async_session):
    """Ensure event_outbox is clean before and after each test."""
    try:
        await async_session.execute(
            text("TRUNCATE TABLE event_outbox RESTART IDENTITY CASCADE")
        )
        await async_session.commit()
    except Exception:
        pass
    yield
    try:
        await async_session.execute(
            text("TRUNCATE TABLE event_outbox RESTART IDENTITY CASCADE")
        )
        await async_session.commit()
    except Exception:
        pass


async def _create_minimal_flow(async_client, headers):
    payload = {
        "name": "Outbox Test Flow",
        "description": "",
        "version": "1.0.0",
        "flow_data": {
            "nodes": [
                {
                    "id": "start",
                    "type": "message",
                    "content": {"messages": [{"type": "text", "content": "hi"}]},
                    "position": {"x": 0, "y": 0},
                }
            ],
            "connections": [],
        },
        "entry_node_id": "start",
        "info": {},
    }
    resp = await async_client.post("/v1/cms/flows", json=payload, headers=headers)
    assert resp.status_code == status.HTTP_201_CREATED
    return resp.json()


class TestFlowOutboxEvents:
    async def test_outbox_on_flow_create(
        self, async_client, async_session, backend_service_account_headers
    ):
        await _create_minimal_flow(async_client, backend_service_account_headers)
        result = await async_session.execute(
            text("SELECT COUNT(*) FROM event_outbox WHERE event_type = 'flow_created'")
        )
        count = int(result.scalar() or 0)
        assert count >= 1, "Expected flow_created event in outbox"

    async def test_outbox_on_flow_update(
        self, async_client, async_session, backend_service_account_headers
    ):
        flow = await _create_minimal_flow(async_client, backend_service_account_headers)
        update = {"description": "updated"}
        resp = await async_client.put(
            f"/v1/cms/flows/{flow['id']}",
            json=update,
            headers=backend_service_account_headers,
        )
        assert resp.status_code == status.HTTP_200_OK
        result = await async_session.execute(
            text("SELECT COUNT(*) FROM event_outbox WHERE event_type = 'flow_updated'")
        )
        count = int(result.scalar() or 0)
        assert count >= 1, "Expected flow_updated event in outbox"

    async def test_outbox_on_flow_publish(
        self, async_client, async_session, backend_service_account_headers
    ):
        flow = await _create_minimal_flow(async_client, backend_service_account_headers)
        resp = await async_client.put(
            f"/v1/cms/flows/{flow['id']}",
            json={"publish": True},
            headers=backend_service_account_headers,
        )
        assert resp.status_code == status.HTTP_200_OK
        result = await async_session.execute(
            text(
                "SELECT COUNT(*) FROM event_outbox WHERE event_type = 'flow_published'"
            )
        )
        count = int(result.scalar() or 0)
        assert count >= 1, "Expected flow_published event in outbox"


@pytest.mark.parametrize("destination", ["audit:flow", "flow_events"])
async def test_flow_audit_delivery_is_durable_and_idempotent(
    async_session, destination
):
    from uuid import NAMESPACE_URL, uuid4, uuid5

    from sqlalchemy import delete, select

    from app.models.event import Event
    from app.models.event_outbox import EventOutbox, EventStatus
    from app.services.event_outbox_service import EventOutboxService

    service = EventOutboxService()
    payload = {"aggregate_id": str(uuid4()), "positions_count": 2}
    event = await service.publish_event(
        async_session, "flow_node_positions_updated", destination, payload
    )
    await async_session.commit()
    audit_id = uuid5(NAMESPACE_URL, f"urn:wriveted:flow-audit:{event.id}")
    try:
        stats = await service.process_pending_events(async_session)
        assert stats["succeeded"] == 1
        audit = await async_session.get(Event, audit_id)
        assert audit.info["payload"] == payload
        assert audit.info["outbox_event_id"] == str(event.id)
        assert event.status == EventStatus.PUBLISHED

        # Replay the retained delivery without duplicating the editorial history.
        event.status = EventStatus.PENDING
        await async_session.commit()
        stats = await service.process_pending_events(async_session)
        assert stats["succeeded"] == 1
        audits = (
            await async_session.scalars(select(Event).where(Event.id == audit_id))
        ).all()
        assert len(audits) == 1
        assert event.status == EventStatus.PUBLISHED

        event.payload = {**payload, "positions_count": 99}
        event.status = EventStatus.PENDING
        await async_session.commit()
        stats = await service.process_pending_events(async_session)
        assert stats["failed"] == 1
        assert event.status == EventStatus.FAILED
        await async_session.refresh(audit)
        assert audit.info["payload"] == payload
    finally:
        await async_session.execute(delete(Event).where(Event.id == audit_id))
        await async_session.execute(
            delete(EventOutbox).where(EventOutbox.id == event.id)
        )
        await async_session.commit()


async def test_flow_audit_failure_rolls_back_audit_and_retries(
    async_session, monkeypatch
):
    from uuid import NAMESPACE_URL, uuid4, uuid5

    from sqlalchemy import delete

    from app.models.event import Event
    from app.models.event_outbox import EventOutbox, EventStatus
    from app.services.event_outbox_service import EventOutboxService

    service = EventOutboxService()
    event = await service.publish_event(
        async_session,
        "flow_updated",
        "audit:flow",
        {"aggregate_id": str(uuid4()), "changes": {"description": "synthetic"}},
    )
    await async_session.commit()
    audit_id = uuid5(NAMESPACE_URL, f"urn:wriveted:flow-audit:{event.id}")
    original = service._mark_event_published

    async def fail_after_publication(db, pending):
        await original(db, pending)
        raise RuntimeError("synthetic publication failure")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(service, "_mark_event_published", fail_after_publication)
            stats = await service.process_pending_events(async_session)
        assert stats["failed"] == 1
        assert event.status == EventStatus.FAILED
        assert event.retry_count == 1
        assert await async_session.get(Event, audit_id) is None
        event.next_retry_at = None
        await async_session.commit()
        stats = await service.process_pending_events(async_session)
        assert stats["succeeded"] == 1
        assert event.status == EventStatus.PUBLISHED
        assert await async_session.get(Event, audit_id) is not None
    finally:
        await async_session.execute(delete(Event).where(Event.id == audit_id))
        await async_session.execute(
            delete(EventOutbox).where(EventOutbox.id == event.id)
        )
        await async_session.commit()


async def test_unknown_flow_audit_event_remains_failed(async_session):
    from uuid import uuid4

    from app.models.event_outbox import EventStatus
    from app.services.event_outbox_service import EventOutboxService

    service = EventOutboxService()
    event = await service.publish_event(
        async_session,
        "unrecognized_editor_event",
        "flow_events",
        {"aggregate_id": str(uuid4())},
    )
    await async_session.commit()
    stats = await service.process_pending_events(async_session)
    assert stats["failed"] == 1
    assert stats["succeeded"] == 0
    assert event.status == EventStatus.FAILED
    assert event.last_error == "Unsupported flow audit event"


async def test_flow_audit_database_error_does_not_record_private_payload(
    async_session, monkeypatch
):
    from uuid import uuid4

    from sqlalchemy.exc import StatementError
    from sqlalchemy.sql.dml import Insert
    from structlog.testing import capture_logs

    from app.services.event_outbox_service import EventOutboxService

    service = EventOutboxService()
    event = await service.publish_event(
        async_session,
        "flow_updated",
        "audit:flow",
        {"aggregate_id": str(uuid4()), "private": "private-flow-marker"},
    )
    await async_session.commit()
    execute = async_session.execute

    async def fail_audit_insert(statement, *args, **kwargs):
        if isinstance(statement, Insert) and statement.table.name == "events":
            raise StatementError(
                "synthetic failure",
                "INSERT INTO events",
                {"payload": "private-flow-marker"},
                RuntimeError("failed"),
            )
        return await execute(statement, *args, **kwargs)

    monkeypatch.setattr(async_session, "execute", fail_audit_insert)
    with capture_logs() as logs:
        stats = await service.process_pending_events(async_session)
    assert "private-flow-marker" not in str(logs)
    assert stats["failed"] == 1
    assert event.last_error == "Flow audit persistence failed (StatementError)"
    assert "private-flow-marker" not in event.last_error

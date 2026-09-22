import json
import logging
from unittest.mock import Mock
from uuid import uuid4

from app.services.event_listener import FlowEvent, FlowEventListener, log_all_events


async def test_routine_flow_events_are_debug_only(caplog):
    event = FlowEvent(
        event_type="session_updated",
        session_id=uuid4(),
        flow_id=uuid4(),
        timestamp=0,
        current_node="ask_age",
    )
    with caplog.at_level(logging.INFO, logger="app.services.event_listener"):
        await log_all_events(event)
    assert not caplog.records

    with caplog.at_level(logging.DEBUG, logger="app.services.event_listener"):
        await log_all_events(event)
    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.message == "Flow event received"
    assert record.event_type == "session_updated"
    assert record.session_id == str(event.session_id)
    assert record.current_node == "ask_age"


async def test_invalid_notification_logs_type_without_payload_or_logging_failure(
    caplog,
):
    listener = FlowEventListener()
    handler = Mock()
    listener.register_handler("*", handler)
    with caplog.at_level(logging.ERROR, logger="app.services.event_listener"):
        await listener._handle_notification(
            None, 0, "flow_events", json.dumps({"private": "must-not-appear"})
        )
    handler.assert_not_called()
    assert len(caplog.records) == 1
    assert caplog.records[0].message == "Invalid flow event notification"
    assert caplog.records[0].error_type == "ValidationError"
    assert "must-not-appear" not in caplog.text


async def test_debug_logging_does_not_prevent_notification_delivery(caplog):
    listener = FlowEventListener()
    handler = Mock()
    listener.register_handler("node_changed", handler)
    event = FlowEvent(
        event_type="node_changed", session_id=uuid4(), flow_id=uuid4(), timestamp=0
    )
    with caplog.at_level(logging.DEBUG, logger="app.services.event_listener"):
        await listener._handle_notification(
            None, 0, "flow_events", event.model_dump_json()
        )
    handler.assert_called_once_with(event)
    assert all(record.levelno < logging.ERROR for record in caplog.records)
    assert caplog.records[-1].event_type == "node_changed"

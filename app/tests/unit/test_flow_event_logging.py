import logging
from uuid import uuid4

from app.services.event_listener import FlowEvent, log_all_events


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

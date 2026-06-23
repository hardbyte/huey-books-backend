"""Regression test: flow-event dispatch must not amplify over time.

`_handle_notification` previously did `handlers = self.handlers.get(type, [])`
(the *live* list) then `handlers.extend(self.handlers.get("*"))`, mutating the
stored list. Each notification re-appended the wildcard handlers, so the
handler list grew unboundedly and every event fanned out to ever-more handlers
— a self-amplifying dispatch storm that degraded long-lived instances.
"""

import json
import uuid

from app.services.event_listener import FlowEventListener


def _payload(event_type: str = "node_changed") -> str:
    return json.dumps(
        {
            "event_type": event_type,
            "session_id": str(uuid.uuid4()),
            "flow_id": str(uuid.uuid4()),
            "timestamp": 0.0,
            "current_node": "show_books",
        }
    )


async def test_repeated_notifications_do_not_grow_handler_lists():
    listener = FlowEventListener()
    calls = {"wildcard": 0, "typed": 0}
    listener.register_handler("*", lambda e: calls.__setitem__("wildcard", calls["wildcard"] + 1))
    listener.register_handler(
        "node_changed", lambda e: calls.__setitem__("typed", calls["typed"] + 1)
    )

    for _ in range(5):
        await listener._handle_notification(None, 0, "flow_events", _payload())

    # Stored handler lists must stay at their registered size (regression: the
    # in-place extend grew node_changed by one wildcard handler per call).
    assert len(listener.handlers["node_changed"]) == 1
    assert len(listener.handlers["*"]) == 1

    # Each handler fires exactly once per notification — not 1, 2, 3, ... times.
    assert calls["typed"] == 5
    assert calls["wildcard"] == 5

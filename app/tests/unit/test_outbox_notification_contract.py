from unittest.mock import AsyncMock, Mock

import pytest

from app.services.event_outbox_service import EventOutboxService


@pytest.mark.asyncio
async def test_outbox_publication_does_not_send_session_notifications():
    db = AsyncMock()
    db.add = Mock()
    event = await EventOutboxService().publish_event(
        db, "flow_updated", "audit:flow", {"aggregate_id": "synthetic"}
    )
    db.add.assert_called_once_with(event)
    db.execute.assert_not_awaited()

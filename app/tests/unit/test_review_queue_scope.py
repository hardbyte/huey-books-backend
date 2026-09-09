from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.models.educator import Educator
from app.models.school_admin import SchoolAdmin
from app.services.review_queues import get_legacy_review_queue
from app.services.workspace_errors import WorkspaceForbidden


@pytest.mark.parametrize("model", [SchoolAdmin, Educator])
async def test_missing_teacher_scope_fails_closed(model):
    session = AsyncMock()
    with pytest.raises(WorkspaceForbidden, match="School membership required"):
        await get_legacy_review_queue(
            session=session, actor=model(school_id=None), school_uuid=uuid4()
        )
    session.scalar.assert_not_awaited()

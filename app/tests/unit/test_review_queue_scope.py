from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.reviews import get_review_queue
from app.models.educator import Educator
from app.models.school_admin import SchoolAdmin


@pytest.mark.parametrize("model", [SchoolAdmin, Educator])
async def test_missing_teacher_scope_fails_closed(model):
    session = AsyncMock()
    with pytest.raises(HTTPException) as error:
        await get_review_queue(
            session=session, account=model(school_id=None), school_id=uuid4()
        )
    assert error.value.status_code == 403
    session.scalar.assert_not_awaited()

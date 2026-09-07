from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.chat import _resolve_school_for_context


@pytest.mark.asyncio
async def test_authenticated_school_overrides_supplied_link():
    school = SimpleNamespace(wriveted_identifier=uuid4())
    db = AsyncMock()
    db.get.return_value = school
    assert (
        await _resolve_school_for_context(
            db,
            SimpleNamespace(school_id=12),
            {"context": {"school_wriveted_id": str(uuid4())}},
        )
        is school
    )
    db.execute.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("context", [[], "bad", {"school_wriveted_id": "x" * 36}])
async def test_invalid_context_never_reaches_database(context):
    db = AsyncMock()
    with pytest.raises(HTTPException) as error:
        await _resolve_school_for_context(db, None, {"context": context})
    assert error.value.status_code == 422
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_school_is_not_attributed():
    db = AsyncMock()
    db.execute.return_value = Mock(scalar_one_or_none=Mock(return_value=None))
    with pytest.raises(HTTPException) as error:
        await _resolve_school_for_context(
            db, None, {"context": {"school_wriveted_id": str(uuid4())}}
        )
    assert error.value.status_code == 404

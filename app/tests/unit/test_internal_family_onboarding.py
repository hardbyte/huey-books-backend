"""
Unit tests for the ``/v1/onboarding/family`` internal handler.

These are pure unit tests: the db session is an AsyncMock, so no real
database is touched.
"""

from unittest.mock import AsyncMock

import pytest

from app.services.internal_api_handlers import handle_family_onboarding


@pytest.mark.asyncio
async def test_family_onboarding_anonymous_session_creates_nothing():
    """When the chat session has no session_user_id, do not create anything."""
    mock_db = AsyncMock()

    result = await handle_family_onboarding(
        mock_db,
        {"parent_name": "Jo", "children": [{"name": "Sam", "age": 8}]},
        {},
        context=None,
    )

    assert result["children_created"] == 0
    mock_db.add.assert_not_called()


@pytest.mark.asyncio
async def test_family_onboarding_empty_context_creates_nothing():
    """An empty context dict (no session_user_id key) is also anonymous."""
    mock_db = AsyncMock()

    result = await handle_family_onboarding(
        mock_db,
        {"parent_name": "Jo", "children": [{"name": "Sam", "age": 8}]},
        {},
        context={},
    )

    assert result["children_created"] == 0
    mock_db.add.assert_not_called()

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import chat
from app.api.dependencies.security import (
    get_current_active_user,
    get_current_active_user_or_service_account,
)
from app.db.session import get_async_session
from app.models.service_account import ServiceAccount, ServiceAccountType
from app.models.user import User, UserAccountType


@pytest.mark.parametrize(
    "principal,expected_status",
    [
        (User(type=UserAccountType.STUDENT), 403),
        (User(type=UserAccountType.EDUCATOR), 403),
        (User(type=UserAccountType.SCHOOL_ADMIN), 403),
        (ServiceAccount(type=ServiceAccountType.SCHOOL), 403),
        (ServiceAccount(type=ServiceAccountType.LMS), 403),
        (ServiceAccount(type=ServiceAccountType.KIOSK), 403),
        (User(type=UserAccountType.WRIVETED), 204),
        (ServiceAccount(type=ServiceAccountType.BACKEND), 204),
    ],
)
def test_only_platform_staff_or_backend_can_delete_sessions(
    principal, expected_status, monkeypatch
):
    app = FastAPI()
    app.include_router(chat.router, prefix="/v1/chat")
    app.dependency_overrides[get_current_active_user] = lambda: principal
    app.dependency_overrides[get_current_active_user_or_service_account] = lambda: (
        principal
    )
    app.dependency_overrides[get_async_session] = lambda: AsyncMock()
    lookup = AsyncMock(return_value=object())
    delete = AsyncMock()
    monkeypatch.setattr(chat.crud.conversation_session, "aget", lookup)
    monkeypatch.setattr(chat.crud.conversation_session, "aremove", delete)
    session_id = uuid4()
    response = TestClient(app).delete(f"/v1/chat/admin/sessions/{session_id}")
    assert response.status_code == expected_status, response.text
    if expected_status == 403:
        lookup.assert_not_awaited()
        delete.assert_not_awaited()
    else:
        delete.assert_awaited_once()
        assert delete.call_args.kwargs["id"] == session_id

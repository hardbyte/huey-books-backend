from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from jose import JWTError, jwt

from app.api.dependencies.security import (
    get_current_active_user,
    get_optional_authenticated_user,
)
from app.api.dependencies.view_as import create_view_as_context, enforce_view_as
from app.api.view_as import router
from app.config import get_settings
from app.db.session import get_session
from app.models.user import UserAccountType
from app.services.security import create_access_token, get_payload_from_access_token


@pytest.fixture
def view_as(monkeypatch):
    actor = SimpleNamespace(
        id=uuid4(), name="Staff", type=UserAccountType.WRIVETED, is_active=True
    )
    target = SimpleNamespace(
        id=uuid4(),
        name="Teacher",
        type=UserAccountType.SCHOOL_ADMIN,
        is_active=True,
        school_id=uuid4(),
    )
    users = {str(user.id): user for user in (actor, target)}
    monkeypatch.setattr("app.crud.user.get", lambda db, id: users.get(str(id)))
    monkeypatch.setattr("app.crud.user.get_or_404", lambda db, id: users[str(id)])
    app = FastAPI(dependencies=[Depends(enforce_view_as)])
    app.dependency_overrides[get_session] = lambda: Mock()
    app.include_router(router, prefix="/v1")

    @app.get("/v1/auth/me")
    def me(user=Depends(get_current_active_user)):
        return {"id": str(user.id), "school_id": str(getattr(user, "school_id", None))}

    @app.get("/v1/works")
    def optional(user=Depends(get_optional_authenticated_user)):
        return {"id": str(user.id)}

    @app.api_route("/v1/work/{work_id}", methods=["POST", "PATCH", "DELETE", "GET"])
    def work(work_id: int, user=Depends(get_current_active_user)):
        return {"id": str(user.id)}

    @app.get("/v1/auth/firebase")
    def side_effect():
        raise AssertionError("Side effect must not run")

    token = create_access_token(f"Wriveted:User-Account:{actor.id}")
    context = create_view_as_context(actor, target)["context"]
    headers = {"Authorization": f"Bearer {token}", "X-View-As": context}
    return TestClient(app), actor, target, headers


def test_effective_user_and_optional_auth(view_as):
    client, actor, target, headers = view_as
    response = client.get("/v1/auth/me", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json() == {"id": str(target.id), "school_id": str(target.school_id)}
    assert client.get("/v1/works", headers=headers).json()["id"] == str(target.id)
    assert client.get(
        "/v1/auth/me", headers={"Authorization": headers["Authorization"]}
    ).json()["id"] == str(actor.id)


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/v1/work/1"),
        ("PATCH", "/v1/work/1"),
        ("DELETE", "/v1/work/1"),
        ("GET", "/v1/auth/firebase"),
    ],
)
def test_read_only_fail_closed(view_as, method, path):
    client, _, _, headers = view_as
    assert client.request(method, path, headers=headers).status_code == 403


@pytest.mark.parametrize(
    "change",
    [
        "actor_inactive",
        "actor_demoted",
        "target_inactive",
        "target_promoted",
        "no_school",
        "wrong_actor",
        "bad_context",
        "expired_actor",
    ],
)
def test_invalid_sessions_fail_closed(view_as, change):
    client, actor, target, headers = view_as
    if change == "actor_inactive":
        actor.is_active = False
    if change == "actor_demoted":
        actor.type = UserAccountType.EDUCATOR
    if change == "target_inactive":
        target.is_active = False
    if change == "target_promoted":
        target.type = UserAccountType.WRIVETED
    if change == "no_school":
        target.school_id = None
    if change == "wrong_actor":
        headers["Authorization"] = (
            f"Bearer {create_access_token(f'Wriveted:User-Account:{target.id}')}"
        )
    if change == "bad_context":
        headers["X-View-As"] += "tamper"
    if change == "expired_actor":
        headers["Authorization"] = (
            f"Bearer {create_access_token(f'Wriveted:User-Account:{actor.id}', expires_delta=timedelta(seconds=-1))}"
        )
    assert client.get("/v1/auth/me", headers=headers).status_code == 403


def test_context_cannot_be_used_as_login_or_nested(view_as):
    client, actor, target, headers = view_as
    with pytest.raises(JWTError):
        get_payload_from_access_token(headers["X-View-As"])
    assert (
        client.post(f"/v1/auth/view-as/{target.id}", headers=headers).status_code == 403
    )
    assert (
        client.post(
            f"/v1/auth/view-as/{target.id}",
            headers={"Authorization": headers["Authorization"]},
        ).status_code
        == 200
    )
    teacher_auth = {
        "Authorization": f"Bearer {create_access_token(f'Wriveted:User-Account:{target.id}')}"
    }
    assert (
        client.post(f"/v1/auth/view-as/{target.id}", headers=teacher_auth).status_code
        == 403
    )


def test_legacy_work_detail_does_not_write(monkeypatch):
    from app.api.works import get_work_by_id
    from app.schemas.work import WorkInfo

    legacy_info = {"other": {"source": "catalogue"}}
    work = SimpleNamespace(id=1, info=legacy_info.copy())
    session = Mock()
    session.scalar.return_value = work
    monkeypatch.setattr("app.api.works.WorkDetail.model_validate", lambda value: value)
    assert get_work_by_id(work=work, full_detail=True, session=session) is work
    session.commit.assert_not_called()
    session.add.assert_not_called()
    assert work.info == legacy_info
    assert WorkInfo.model_validate(work.info).genres == []
    assert WorkInfo.model_validate(work.info).other == {"source": "catalogue"}


def test_expired_context_is_rejected(view_as):
    client, _, _, headers = view_as
    claims = jwt.get_unverified_claims(headers["X-View-As"])
    claims["exp"] = 1
    headers["X-View-As"] = jwt.encode(
        claims, get_settings().SECRET_KEY, algorithm="HS256"
    )
    assert client.get("/v1/auth/me", headers=headers).status_code == 403


def test_context_responses_are_not_cacheable(view_as):
    client, _, target, headers = view_as
    assert (
        client.get("/v1/auth/me", headers=headers).headers["cache-control"]
        == "no-store"
    )
    response = client.post(
        f"/v1/auth/view-as/{target.id}",
        headers={"Authorization": headers["Authorization"]},
    )
    assert response.headers["cache-control"] == "no-store"


def test_ordinary_responses_vary_before_view_as_starts(view_as):
    client, actor, _, headers = view_as
    response = client.get(
        "/v1/auth/me", headers={"Authorization": headers["Authorization"]}
    )
    assert response.status_code == 200
    assert response.json()["id"] == str(actor.id)
    assert {"x-view-as", "authorization"} <= {
        header.strip().lower() for header in response.headers.get("vary", "").split(",")
    }
    assert response.headers["cache-control"] == "no-store"

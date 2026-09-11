import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt
from pydantic import ValidationError

from app.api import chat
from app.config import get_settings
from app.db.session import get_async_session
from app.middleware.browser_timing_receipt import BrowserTimingReceiptMiddleware
from app.middleware.request_body_limit import RequestBodyLimitMiddleware
from app.middleware.request_logging import RequestLoggingMiddleware
from app.models.cms import SessionStatus
from app.schemas.browser_timing import BrowserTiming
from app.services import browser_timing

TIMING = {
    "operation": "interact",
    "response_to_commit_ms": 2.5,
    "response_to_next_frame_ms": 16.5,
    "request_duration_ms": 250.0,
}


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(chat.router, prefix="/v1/chat")
    app.add_middleware(
        RequestBodyLimitMiddleware, paths={"/v1/chat/telemetry"}, max_bytes=1024
    )
    app.add_middleware(BrowserTimingReceiptMiddleware, secret_key="test-signing-secret")
    app.add_middleware(RequestLoggingMiddleware)
    app.dependency_overrides[get_async_session] = lambda: AsyncMock()
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(
        SECRET_KEY="test-signing-secret"
    )
    monkeypatch.setattr(
        chat, "browser_timing_service", browser_timing.BrowserTimingService()
    )
    monkeypatch.setattr(
        chat.chat_repo,
        "get_session_by_token",
        AsyncMock(
            return_value=SimpleNamespace(id=uuid4(), status=SessionStatus.ACTIVE)
        ),
    )
    monkeypatch.setattr(
        chat.chat_runtime,
        "process_interaction",
        AsyncMock(return_value={"messages": []}),
    )
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize(
    "path",
    ["/v1/chat/session/interact", "/v1/chat/sessions/test-session-token/interact"],
)
def test_new_and_legacy_interactions_preserve_credentials_csrf_and_issue_receipt(
    client, path
):
    headers = {"X-Chat-Session": "test-session-token", "X-CSRF-Token": "test-csrf"}
    response = client.post(
        path, headers=headers, json={"input": "a choice", "input_type": "choice"}
    )
    assert response.status_code == 200, response.text
    chat.chat_repo.get_session_by_token.assert_awaited_once()
    assert (
        chat.chat_repo.get_session_by_token.call_args.kwargs["session_token"]
        == "test-session-token"
    )
    receipt = response.headers["X-Response-Timing-Token"]
    claims = jwt.decode(
        receipt,
        "test-signing-secret",
        algorithms=["HS256"],
        audience="browser-response-timing",
    )
    assert claims["sub"] == response.headers["X-Request-ID"]
    assert "test-session-token" not in json.dumps(claims)
    assert claims["operation"] == "interact"
    result = client.post(
        "/v1/chat/telemetry", headers={"X-Response-Timing-Token": receipt}, json=TIMING
    )
    assert result.status_code == 204
    assert (
        client.post(
            path,
            headers={"X-Chat-Session": "test-session-token"},
            json={"input": "a choice"},
        ).status_code
        == 403
    )


def test_legacy_interaction_does_not_require_new_header(client):
    response = client.post(
        "/v1/chat/sessions/test-session-token/interact",
        headers={"X-CSRF-Token": "test-csrf"},
        json={"input": "a choice", "input_type": "choice"},
    )
    assert response.status_code == 200, response.text


def test_legacy_openapi_still_declares_path_credentials(client):
    paths = client.get("/openapi.json").json()["paths"]
    for path, methods in paths.items():
        if "{session_token}" not in path:
            continue
        for operation in methods.values():
            assert operation["deprecated"] is True
            assert any(
                parameter["name"] == "session_token"
                and parameter["in"] == "path"
                and parameter["required"]
                for parameter in operation["parameters"]
            )


@pytest.mark.parametrize(
    "headers, expected", [({}, 401), ({"X-Chat-Session": "x" * 129}, 422)]
)
def test_header_session_rejects_invalid_credentials(client, headers, expected):
    response = client.post(
        "/v1/chat/session/interact",
        headers={"X-CSRF-Token": "test", **headers},
        json={"input": "hello", "input_type": "text"},
    )
    assert response.status_code == expected
    chat.chat_repo.get_session_by_token.assert_not_called()


def test_conflicting_and_non_ascii_legacy_credentials_rejected_without_500(client):
    for path, header, status in [("token", "other", 400), ("%C3%A9", "other", 401)]:
        response = client.post(
            f"/v1/chat/sessions/{path}/interact",
            headers={"X-Chat-Session": header},
            json={"input": "hello"},
        )
        assert response.status_code == status, response.text
    chat.chat_repo.get_session_by_token.assert_not_called()


def test_unknown_session_is_not_authenticated_by_csrf_alone(client):
    chat.chat_repo.get_session_by_token.return_value = None
    response = client.post(
        "/v1/chat/session/interact",
        headers={"X-Chat-Session": "unknown", "X-CSRF-Token": "test"},
        json={"input": "hello", "input_type": "text"},
    )
    assert response.status_code == 404
    assert "X-Response-Timing-Token" not in response.headers
    chat.chat_runtime.process_interaction.assert_not_called()


def test_telemetry_requires_signed_receipt_and_limits_body(client):
    assert client.post("/v1/chat/telemetry", json=TIMING).status_code == 422
    assert (
        client.post(
            "/v1/chat/telemetry",
            json=TIMING,
            headers={"X-Response-Timing-Token": "invalid"},
        ).status_code
        == 401
    )
    response = client.post(
        "/v1/chat/telemetry",
        content=b" " * 1025,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413
    assert "X-Request-ID" in response.headers


@pytest.mark.parametrize(
    "field,value",
    [
        ("operation", "arbitrary"),
        ("response_to_commit_ms", -1),
        ("response_to_next_frame_ms", 60001),
        ("request_duration_ms", float("nan")),
        ("request_duration_ms", float("inf")),
        ("session_token", "secret"),
    ],
)
def test_timing_schema_is_bounded_and_allowlisted(field, value):
    with pytest.raises(ValidationError):
        BrowserTiming.model_validate({**TIMING, field: value})


def test_receipts_expire_are_operation_bound_and_replays_are_bounded(monkeypatch):
    log = []
    monkeypatch.setattr(
        browser_timing.logger, "info", lambda event, **fields: log.append(fields)
    )
    service = browser_timing.BrowserTimingService()
    secret = "secret"
    timing = BrowserTiming(**TIMING)
    receipt = browser_timing.create_timing_receipt(str(uuid4()), "interact", secret)
    service.record(timing, receipt, secret)
    service.record(timing, receipt, secret)
    assert len(log) == 1
    with pytest.raises(browser_timing.InvalidTimingReceipt):
        service.record(
            BrowserTiming(**{**TIMING, "operation": "start"}), receipt, secret
        )
    expired = jwt.encode(
        {
            "aud": "browser-response-timing",
            "sub": str(uuid4()),
            "operation": "interact",
            "exp": 1,
        },
        secret,
        algorithm="HS256",
    )
    with pytest.raises(browser_timing.InvalidTimingReceipt):
        service.record(timing, expired, secret)
    with pytest.raises(browser_timing.InvalidTimingReceipt):
        service.record(timing, receipt, "different-key")
    monkeypatch.setattr(browser_timing, "MAX_RECENT_RECEIPTS", 1)
    service.record(
        timing,
        browser_timing.create_timing_receipt(str(uuid4()), "interact", secret),
        secret,
    )
    assert len(service._recent) == len(log) == 1

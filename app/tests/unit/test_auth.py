import datetime
import time
from uuid import uuid4

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from jose import ExpiredSignatureError, JWTError, jwt
from pydantic import ValidationError
from pytest import approx

from app.api.dependencies.security import create_user_access_token, get_valid_token_data
from app.api.oauth import _redirect_uri_allowed
from app.config import get_settings
from app.models import User
from app.services.security import (
    create_access_token,
    get_payload_from_access_token,
    get_raw_payload_from_access_token,
)


def test_kid_bearing_oauth_token_rejected_on_rest():
    # OAuth (MCP) tokens carry a `kid`; they must not be accepted by the REST
    # user pipeline (they are verified/confined in-process by the MCP instead).
    token = jwt.encode(
        {"sub": "Wriveted:User-Account:1"},
        "a-different-signing-key",
        algorithm="HS256",
        headers={"kid": "some-oauth-kid"},
    )
    with pytest.raises(JWTError):
        get_raw_payload_from_access_token(token)


def test_redirect_uri_lookalikes_rejected():
    allowed = ["https://mcp.hueybooks.com/auth/callback"]
    assert _redirect_uri_allowed("http://127.0.0.1:9876/cb", allowed)
    assert _redirect_uri_allowed("http://localhost:1/cb", allowed)
    assert _redirect_uri_allowed(allowed[0], allowed)
    # Lookalikes must NOT pass.
    assert not _redirect_uri_allowed("http://localhost.attacker.com/cb", allowed)
    assert not _redirect_uri_allowed("http://127.0.0.1@attacker.com/cb", allowed)
    assert not _redirect_uri_allowed("https://evil.example.com/cb", allowed)


def test_create_token():
    test_user = User(id=uuid4())
    token = create_user_access_token(test_user)
    payload = get_payload_from_access_token(token)
    assert payload.sub == f"Wriveted:User-Account:{test_user.id}".title()
    assert isinstance(payload.exp, datetime.datetime)
    assert isinstance(payload.iat, datetime.datetime)
    assert payload.iat < payload.exp

    valid_for = payload.exp - payload.iat
    assert valid_for.total_seconds() / 60 == approx(
        float(get_settings().ACCESS_TOKEN_EXPIRE_MINUTES)
    )


def test_extra_claims_propogated():
    token = create_access_token(
        subject="Wriveted:User-Account:0",
        extra_claims={"test-claim": "secret"},
        expires_delta=datetime.timedelta(minutes=1),
    )

    raw_payload = get_raw_payload_from_access_token(token)

    assert raw_payload["sub"] == "Wriveted:User-Account:0"
    assert "test-claim" in raw_payload
    assert raw_payload["test-claim"] == "secret"


def test_token_with_invalid_subject_rejected():
    token = create_access_token(
        subject="test-subject", expires_delta=datetime.timedelta(seconds=60)
    )
    with pytest.raises(ValidationError):
        get_payload_from_access_token(token)


def test_expired_token_rejected():
    token = create_access_token(
        subject=f"Wriveted:user-account:{uuid4()}",
        expires_delta=datetime.timedelta(seconds=1),
    )
    get_payload_from_access_token(token)
    time.sleep(2)

    with pytest.raises(ExpiredSignatureError):
        get_payload_from_access_token(token)


@pytest.mark.parametrize(
    "subject",
    [
        "wriveted",
        "other:user-account:" + str(uuid4()),
        "wriveted:unknown:" + str(uuid4()),
        "wriveted:user-account:not-a-uuid",
        "wriveted:user-account:",
        "wriveted:user-account:" + str(uuid4()) + ":extra",
    ],
)
def test_malformed_subject_is_unauthorized(subject):
    app = FastAPI()

    @app.get("/protected")
    async def protected(payload=Depends(get_valid_token_data)):
        return {"subject": payload.sub}

    response = TestClient(app).get(
        "/protected",
        headers={"Authorization": f"Bearer {create_access_token(subject)}"},
    )
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("kind", ["user-account", "service-account"])
def test_valid_account_subject_retains_identity(kind):
    identifier = uuid4()
    payload = get_payload_from_access_token(
        create_access_token(f"Wriveted:{kind}:{identifier}")
    )
    assert payload.sub.lower() == f"wriveted:{kind}:{identifier}"

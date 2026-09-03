"""Integration tests for the OAuth authorization-code + refresh-token flow.

Exercises the security-critical paths: PKCE, single-use codes, redirect binding,
rotating refresh with family reuse-detection, and token-endpoint client auth.
"""

import hashlib

import pytest

from app import crud
from app.config import get_settings
from app.schemas.users.user_create import UserCreateIn
from app.services.oauth import grants, tokens
from app.services.oauth.grants import OAuthError
from app.tests.util.random_strings import random_lower_string

CLIENT_ID = "mcp-proxy"
REDIRECT = "https://mcp.hueybooks.com/auth/callback"
SCHOOL = "11111111-1111-1111-1111-111111111111"


def _pkce() -> tuple[str, str]:
    verifier = random_lower_string(64)
    challenge = tokens._b64url_no_pad(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


def _make_user(session):
    user = crud.user.create(
        db=session,
        obj_in=UserCreateIn(
            name="oauth flow test",
            email=f"{random_lower_string(8)}@test.com",
            first_name="O",
            last_name_initial="A",
        ),
    )
    session.commit()
    return user


async def _new_code(async_session, user_id, *, redirect=REDIRECT, scopes=None):
    verifier, challenge = _pkce()
    code = await grants.create_authorization_code(
        async_session,
        user_id=str(user_id),
        school_id=SCHOOL,
        client_id=CLIENT_ID,
        redirect_uri=redirect,
        scopes=scopes or [tokens.SCOPE_CATALOGUE_READ, tokens.SCOPE_BOOKS_LABEL],
        code_challenge=challenge,
    )
    return code, verifier


@pytest.mark.asyncio
async def test_authorization_code_happy_path(session, async_session):
    user = _make_user(session)
    code, verifier = await _new_code(async_session, user.id)
    result = await grants.exchange_authorization_code(
        async_session, code=code, redirect_uri=REDIRECT, code_verifier=verifier, client_id=CLIENT_ID
    )
    assert result["token_type"] == "Bearer"
    claims = tokens.decode_access_token(result["access_token"])
    assert claims["sub"] == str(user.id)
    assert claims["school_id"] == SCHOOL
    assert "catalogue:read" in claims["scope"]
    assert result["refresh_token"]


@pytest.mark.asyncio
async def test_pkce_mismatch_rejected(session, async_session):
    user = _make_user(session)
    code, _ = await _new_code(async_session, user.id)
    with pytest.raises(OAuthError) as exc:
        await grants.exchange_authorization_code(
            async_session, code=code, redirect_uri=REDIRECT, code_verifier="wrong-verifier", client_id=CLIENT_ID
        )
    assert exc.value.error == "invalid_grant"


@pytest.mark.asyncio
async def test_code_is_single_use(session, async_session):
    user = _make_user(session)
    code, verifier = await _new_code(async_session, user.id)
    await grants.exchange_authorization_code(
        async_session, code=code, redirect_uri=REDIRECT, code_verifier=verifier, client_id=CLIENT_ID
    )
    with pytest.raises(OAuthError) as exc:
        await grants.exchange_authorization_code(
            async_session, code=code, redirect_uri=REDIRECT, code_verifier=verifier, client_id=CLIENT_ID
        )
    assert exc.value.error == "invalid_grant"


@pytest.mark.asyncio
async def test_redirect_uri_must_match(session, async_session):
    user = _make_user(session)
    code, verifier = await _new_code(async_session, user.id)
    with pytest.raises(OAuthError) as exc:
        await grants.exchange_authorization_code(
            async_session, code=code, redirect_uri="https://evil.example/cb", code_verifier=verifier, client_id=CLIENT_ID
        )
    assert exc.value.error == "invalid_grant"


@pytest.mark.asyncio
async def test_refresh_rotation_and_reuse_detection(session, async_session):
    user = _make_user(session)
    code, verifier = await _new_code(async_session, user.id)
    first = await grants.exchange_authorization_code(
        async_session, code=code, redirect_uri=REDIRECT, code_verifier=verifier, client_id=CLIENT_ID
    )
    # Rotate: old refresh consumed, new one issued.
    second = await grants.rotate_refresh_token(
        async_session, refresh_token=first["refresh_token"], client_id=CLIENT_ID
    )
    assert second["refresh_token"] != first["refresh_token"]

    # Reusing the FIRST (now-consumed) refresh token is theft -> family revoked.
    with pytest.raises(OAuthError) as exc:
        await grants.rotate_refresh_token(
            async_session, refresh_token=first["refresh_token"], client_id=CLIENT_ID
        )
    assert exc.value.error == "invalid_grant"

    # ...and the freshly-issued token is now dead too (grant revoked).
    with pytest.raises(OAuthError):
        await grants.rotate_refresh_token(
            async_session, refresh_token=second["refresh_token"], client_id=CLIENT_ID
        )


def test_token_endpoint_rejects_bad_client(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "OAUTH_MCP_CLIENT_SECRET", "correct-secret")
    resp = client.post(
        "/v1/oauth/token",
        data={"grant_type": "authorization_code", "client_id": CLIENT_ID, "client_secret": "wrong"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"] == "invalid_client"


def test_token_endpoint_unsupported_grant(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "OAUTH_MCP_CLIENT_SECRET", "correct-secret")
    resp = client.post(
        "/v1/oauth/token",
        data={"grant_type": "password", "client_id": CLIENT_ID, "client_secret": "correct-secret"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "unsupported_grant_type"

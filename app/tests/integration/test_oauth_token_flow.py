"""Integration tests for the OAuth authorization-code + refresh-token flow.

Exercises the security-critical paths: PKCE, single-use (incl. true concurrency),
redirect binding, rotating refresh with a reuse grace window and family
reuse-detection, client auth (post + basic), and malformed input handling.
"""

import asyncio
import datetime
import hashlib

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import crud
from app.config import get_settings
from app.models.oauth import OAuthRefreshToken
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
            async_session, code=code, redirect_uri=REDIRECT, code_verifier="a" * 50, client_id=CLIENT_ID
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
async def test_concurrent_exchange_only_one_wins(session, async_session):
    """Two simultaneous redemptions of one code: exactly one succeeds (B2)."""
    user = _make_user(session)
    code, verifier = await _new_code(async_session, user.id)

    async def _exchange():
        engine = create_async_engine(
            get_settings().SQLALCHEMY_ASYNC_URI, pool_size=1, max_overflow=0
        )
        maker = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with maker() as s:
                return await grants.exchange_authorization_code(
                    s, code=code, redirect_uri=REDIRECT, code_verifier=verifier, client_id=CLIENT_ID
                )
        finally:
            await engine.dispose()

    results = await asyncio.gather(_exchange(), _exchange(), return_exceptions=True)
    successes = [r for r in results if isinstance(r, dict)]
    failures = [r for r in results if isinstance(r, OAuthError)]
    assert len(successes) == 1
    assert len(failures) == 1


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
async def test_client_id_mismatch_rejected(session, async_session):
    user = _make_user(session)
    code, verifier = await _new_code(async_session, user.id)
    with pytest.raises(OAuthError) as exc:
        await grants.exchange_authorization_code(
            async_session, code=code, redirect_uri=REDIRECT, code_verifier=verifier, client_id="someone-else"
        )
    assert exc.value.error == "invalid_client"


@pytest.mark.asyncio
async def test_non_ascii_inputs_rejected_not_500(session, async_session):
    user = _make_user(session)
    code, _ = await _new_code(async_session, user.id)
    with pytest.raises(OAuthError) as exc:
        await grants.exchange_authorization_code(
            async_session, code="ünīcode", redirect_uri=REDIRECT, code_verifier="ünīcode-verifier", client_id=CLIENT_ID
        )
    assert exc.value.error == "invalid_grant"


@pytest.mark.asyncio
async def test_refresh_reuse_within_grace_succeeds(session, async_session):
    """A just-rotated token presented again inside the grace window is a benign
    cross-instance race, not theft (H1) -> a fresh pair is issued."""
    user = _make_user(session)
    code, verifier = await _new_code(async_session, user.id)
    first = await grants.exchange_authorization_code(
        async_session, code=code, redirect_uri=REDIRECT, code_verifier=verifier, client_id=CLIENT_ID
    )
    await grants.rotate_refresh_token(async_session, refresh_token=first["refresh_token"], client_id=CLIENT_ID)
    # Immediately reuse the now-consumed first token: within grace -> success.
    regraced = await grants.rotate_refresh_token(
        async_session, refresh_token=first["refresh_token"], client_id=CLIENT_ID
    )
    assert regraced["access_token"]


@pytest.mark.asyncio
async def test_refresh_reuse_outside_grace_revokes_family(session, async_session):
    user = _make_user(session)
    code, verifier = await _new_code(async_session, user.id)
    first = await grants.exchange_authorization_code(
        async_session, code=code, redirect_uri=REDIRECT, code_verifier=verifier, client_id=CLIENT_ID
    )
    second = await grants.rotate_refresh_token(
        async_session, refresh_token=first["refresh_token"], client_id=CLIENT_ID
    )
    # Age the consumed first token past the grace window.
    row = (
        await async_session.execute(
            select(OAuthRefreshToken).where(
                OAuthRefreshToken.token_hash == tokens.hash_refresh_token(first["refresh_token"])
            )
        )
    ).scalar_one()
    row.consumed_at = datetime.datetime.now(datetime.UTC).replace(tzinfo=None) - datetime.timedelta(minutes=5)
    await async_session.commit()

    with pytest.raises(OAuthError) as exc:
        await grants.rotate_refresh_token(async_session, refresh_token=first["refresh_token"], client_id=CLIENT_ID)
    assert exc.value.error == "invalid_grant"
    # Family revoked: the freshly-issued token is now dead too.
    with pytest.raises(OAuthError):
        await grants.rotate_refresh_token(async_session, refresh_token=second["refresh_token"], client_id=CLIENT_ID)


@pytest.mark.asyncio
async def test_revoked_grant_rejects_rotation(session, async_session):
    user = _make_user(session)
    code, verifier = await _new_code(async_session, user.id)
    first = await grants.exchange_authorization_code(
        async_session, code=code, redirect_uri=REDIRECT, code_verifier=verifier, client_id=CLIENT_ID
    )
    grant_id = tokens.decode_access_token(first["access_token"])["grant_id"]
    await grants.revoke_grant(async_session, grant_id)
    with pytest.raises(OAuthError):
        await grants.rotate_refresh_token(async_session, refresh_token=first["refresh_token"], client_id=CLIENT_ID)


def _basic(client_id, secret):
    import base64

    return "Basic " + base64.b64encode(f"{client_id}:{secret}".encode()).decode()


def test_token_endpoint_rejects_bad_client(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "OAUTH_MCP_CLIENT_SECRET", "correct-secret")
    resp = client.post(
        "/v1/oauth/token",
        data={"grant_type": "authorization_code", "client_id": CLIENT_ID, "client_secret": "wrong"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"] == "invalid_client"


def test_token_endpoint_basic_auth(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "OAUTH_MCP_CLIENT_SECRET", "correct-secret")
    # Correct client via HTTP Basic, then an unsupported grant -> proves auth passed.
    resp = client.post(
        "/v1/oauth/token",
        data={"grant_type": "password"},
        headers={"Authorization": _basic(CLIENT_ID, "correct-secret")},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "unsupported_grant_type"
    # Wrong secret via Basic -> 401.
    bad = client.post(
        "/v1/oauth/token",
        data={"grant_type": "password"},
        headers={"Authorization": _basic(CLIENT_ID, "nope")},
    )
    assert bad.status_code == 401

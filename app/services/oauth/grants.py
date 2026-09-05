"""Authorization-code and refresh-token flows over the OAuth persistence.

Security properties:
  * Codes and refresh tokens are looked up by SHA-256 hash; the plaintext exists
    only in the response to the client.
  * Single-use is enforced ATOMICALLY: the consume is a conditional
    ``UPDATE ... WHERE not-yet-used ... RETURNING``, so two concurrent redemptions
    cannot both win (a plain SELECT-then-UPDATE would race under READ COMMITTED).
  * Authorization codes are single-use, short-lived, and bound to the exact
    redirect_uri and a PKCE challenge. Redeeming a used code revokes its grant.
  * Refresh tokens rotate. Presenting an already-rotated token inside a short
    grace window is treated as a benign cross-instance race (a fresh pair is
    issued); outside the window it is theft and the whole family + grant is
    revoked. An absolute cap bounds how long rotation can extend a login.
Errors are raised as ``OAuthError`` carrying an RFC 6749 error code.
"""

from __future__ import annotations

import datetime
import hashlib
import re
import secrets
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.oauth import OAuthAuthorizationCode, OAuthGrant, OAuthRefreshToken
from app.services.oauth import tokens

AUTHORIZATION_CODE_TTL_SECONDS = 60

# token_urlsafe alphabet; PKCE verifier per RFC 7636 section 4.1.
_OPAQUE_RE = re.compile(r"^[A-Za-z0-9_-]{1,512}$")
_VERIFIER_RE = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")


class OAuthError(Exception):
    def __init__(self, error: str, description: str = ""):
        super().__init__(f"{error}: {description}")
        self.error = error
        self.description = description


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _require(pattern: re.Pattern, value: str, what: str) -> None:
    # Reject malformed/non-ASCII input before it reaches .encode("ascii").
    if not value or not pattern.match(value):
        raise OAuthError("invalid_grant", f"malformed {what}")


async def create_authorization_code(
    db: AsyncSession,
    *,
    user_id: str,
    school_id: str,
    client_id: str,
    redirect_uri: str,
    scopes: list[str],
    code_challenge: str,
    code_challenge_method: str = "S256",
) -> str:
    """Record consent (a grant) and issue a one-time authorization code."""
    if code_challenge_method != "S256" or not code_challenge:
        raise OAuthError("invalid_request", "PKCE S256 challenge required")

    grant = OAuthGrant(
        user_id=user_id,
        school_id=school_id,
        client_id=client_id,
        scopes=" ".join(scopes),
    )
    db.add(grant)
    await db.flush()

    code = secrets.token_urlsafe(32)
    db.add(
        OAuthAuthorizationCode(
            code_hash=_sha256(code),
            grant_id=grant.id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            scopes=" ".join(scopes),
            expires_at=_now()
            + datetime.timedelta(seconds=AUTHORIZATION_CODE_TTL_SECONDS),
        )
    )
    await db.commit()
    return code


async def _issue_tokens(
    db: AsyncSession, grant: OAuthGrant, scopes: list[str], family_id
) -> dict:
    settings = get_settings()
    access_token = tokens.mint_access_token(
        user_id=str(grant.user_id),
        school_id=str(grant.school_id),
        scopes=scopes,
        grant_id=str(grant.id),
    )
    refresh_token = tokens.new_refresh_token()
    db.add(
        OAuthRefreshToken(
            token_hash=tokens.hash_refresh_token(refresh_token),
            grant_id=grant.id,
            family_id=family_id,
            scopes=" ".join(scopes),
            expires_at=_now()
            + datetime.timedelta(seconds=settings.OAUTH_REFRESH_TOKEN_TTL_SECONDS),
        )
    )
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "Bearer",
        "expires_in": settings.OAUTH_ACCESS_TOKEN_TTL_SECONDS,
        "scope": " ".join(scopes),
    }


async def _revoke_grant_and_tokens(db: AsyncSession, grant_id) -> None:
    now = _now()
    await db.execute(
        update(OAuthGrant)
        .where(OAuthGrant.id == grant_id, OAuthGrant.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    await db.execute(
        update(OAuthRefreshToken)
        .where(
            OAuthRefreshToken.grant_id == grant_id,
            OAuthRefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )


async def exchange_authorization_code(
    db: AsyncSession,
    *,
    code: str,
    redirect_uri: str,
    code_verifier: str,
    client_id: str,
) -> dict:
    _require(_OPAQUE_RE, code, "authorization code")
    _require(_VERIFIER_RE, code_verifier, "code_verifier")
    now = _now()
    code_hash = _sha256(code)

    # Atomically claim the code (single winner). Failures after this leave the
    # code burned, which is correct.
    claimed = (
        await db.execute(
            update(OAuthAuthorizationCode)
            .where(
                OAuthAuthorizationCode.code_hash == code_hash,
                OAuthAuthorizationCode.used_at.is_(None),
            )
            .values(used_at=now)
            .returning(OAuthAuthorizationCode)
        )
    ).scalar_one_or_none()

    if claimed is None:
        # Unknown code, or an already-used one being replayed. Replay of a used
        # code revokes any tokens issued from its grant (OAuth 2.1 4.1.2).
        grant_id = (
            await db.execute(
                select(OAuthAuthorizationCode.grant_id).where(
                    OAuthAuthorizationCode.code_hash == code_hash
                )
            )
        ).scalar_one_or_none()
        if grant_id is not None:
            await _revoke_grant_and_tokens(db, grant_id)
            await db.commit()
        raise OAuthError("invalid_grant", "invalid authorization code")

    # Capture before commit (expire_on_commit would otherwise trigger IO).
    grant_id = claimed.grant_id
    redirect = claimed.redirect_uri
    challenge = claimed.code_challenge
    expires_at = claimed.expires_at
    scopes = claimed.scopes.split() if claimed.scopes else []
    await db.commit()  # persist the burn

    grant = (
        await db.execute(select(OAuthGrant).where(OAuthGrant.id == grant_id))
    ).scalar_one()
    if grant.revoked_at is not None:
        raise OAuthError("invalid_grant", "grant revoked")
    if grant.client_id != client_id:
        raise OAuthError("invalid_client", "client mismatch")
    if expires_at < now:
        raise OAuthError("invalid_grant", "authorization code expired")
    if redirect != redirect_uri:
        raise OAuthError("invalid_grant", "redirect_uri mismatch")
    if not tokens.verify_pkce(code_verifier, challenge):
        raise OAuthError("invalid_grant", "PKCE verification failed")

    result = await _issue_tokens(db, grant, scopes, family_id=uuid.uuid4())
    await db.commit()
    return result


async def rotate_refresh_token(
    db: AsyncSession, *, refresh_token: str, client_id: str
) -> dict:
    _require(_OPAQUE_RE, refresh_token, "refresh token")
    settings = get_settings()
    now = _now()
    token_hash = tokens.hash_refresh_token(refresh_token)

    # Atomically claim an unused, unrevoked token.
    claimed = (
        await db.execute(
            update(OAuthRefreshToken)
            .where(
                OAuthRefreshToken.token_hash == token_hash,
                OAuthRefreshToken.consumed_at.is_(None),
                OAuthRefreshToken.revoked_at.is_(None),
            )
            .values(consumed_at=now)
            .returning(OAuthRefreshToken)
        )
    ).scalar_one_or_none()

    if claimed is None:
        return await _rotate_unclaimed(db, token_hash, client_id, now)

    grant_id = claimed.grant_id
    family_id = claimed.family_id
    expires_at = claimed.expires_at
    scopes = claimed.scopes.split() if claimed.scopes else []
    await db.commit()

    grant = (
        await db.execute(select(OAuthGrant).where(OAuthGrant.id == grant_id))
    ).scalar_one()
    if grant.revoked_at is not None:
        raise OAuthError("invalid_grant", "grant revoked")
    if grant.client_id != client_id:
        raise OAuthError("invalid_client", "client mismatch")
    if expires_at < now:
        raise OAuthError("invalid_grant", "refresh token expired")
    # Absolute cap: rotation cannot extend a login past this.
    absolute_end = grant.created_at + datetime.timedelta(
        seconds=settings.OAUTH_REFRESH_ABSOLUTE_TTL_SECONDS
    )
    if now >= absolute_end:
        await _revoke_grant_and_tokens(db, grant_id)
        await db.commit()
        raise OAuthError("invalid_grant", "login expired, re-authentication required")

    result = await _issue_tokens(db, grant, scopes, family_id=family_id)
    await db.commit()
    return result


async def _rotate_unclaimed(
    db: AsyncSession, token_hash: str, client_id: str, now: datetime.datetime
) -> dict:
    """The token was not claimable: unknown, revoked, or already consumed."""
    settings = get_settings()
    row = (
        await db.execute(
            select(OAuthRefreshToken).where(OAuthRefreshToken.token_hash == token_hash)
        )
    ).scalar_one_or_none()
    if row is None:
        raise OAuthError("invalid_grant", "unknown refresh token")
    if row.revoked_at is not None:
        raise OAuthError("invalid_grant", "refresh token revoked")

    grant = (
        await db.execute(select(OAuthGrant).where(OAuthGrant.id == row.grant_id))
    ).scalar_one()

    # Consumed within the grace window: a benign cross-instance rotation race
    # (FastMCP's proxy may present a just-rotated token). Issue a fresh pair in
    # the same family without treating it as theft.
    grace = datetime.timedelta(seconds=settings.OAUTH_REFRESH_REUSE_GRACE_SECONDS)
    absolute_end = grant.created_at + datetime.timedelta(
        seconds=settings.OAUTH_REFRESH_ABSOLUTE_TTL_SECONDS
    )
    if (
        row.consumed_at is not None
        and now - row.consumed_at <= grace
        and now <= row.expires_at  # never re-issue on an expired token
        and now < absolute_end  # nor past the login's absolute cap
        and grant.revoked_at is None
        and grant.client_id == client_id
    ):
        scopes = row.scopes.split() if row.scopes else []
        result = await _issue_tokens(db, grant, scopes, family_id=row.family_id)
        await db.commit()
        return result

    # Outside the grace window: token theft. Revoke the whole family and grant.
    await _revoke_grant_and_tokens(db, grant.id)
    await db.commit()
    raise OAuthError("invalid_grant", "refresh token reuse detected")


async def revoke_grant(db: AsyncSession, grant_id) -> None:
    """User-driven 'disconnect this app'. Access tokens die within their TTL."""
    await _revoke_grant_and_tokens(db, grant_id)
    await db.commit()

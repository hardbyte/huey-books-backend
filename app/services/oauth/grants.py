"""Authorization-code and refresh-token flows over the OAuth persistence.

Design points that matter for security:
  * Codes and refresh tokens are looked up by SHA-256 hash; the plaintext exists
    only in the response to the client.
  * Authorization codes are single-use, short-lived, and bound to the exact
    redirect_uri and a PKCE challenge.
  * Refresh tokens rotate: each use consumes the old token and issues a new one
    in the same family. Presenting an already-consumed token is treated as theft
    — the whole family and its grant are revoked.
Errors are raised as ``OAuthError`` carrying an RFC 6749 error code.
"""

from __future__ import annotations

import datetime
import hashlib
import secrets
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.oauth import OAuthAuthorizationCode, OAuthGrant, OAuthRefreshToken
from app.services.oauth import tokens

AUTHORIZATION_CODE_TTL_SECONDS = 60


class OAuthError(Exception):
    def __init__(self, error: str, description: str = ""):
        super().__init__(f"{error}: {description}")
        self.error = error
        self.description = description


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


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


async def exchange_authorization_code(
    db: AsyncSession,
    *,
    code: str,
    redirect_uri: str,
    code_verifier: str,
    client_id: str,
) -> dict:
    row = (
        await db.execute(
            select(OAuthAuthorizationCode).where(
                OAuthAuthorizationCode.code_hash == _sha256(code)
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise OAuthError("invalid_grant", "unknown authorization code")
    if row.used_at is not None:
        raise OAuthError("invalid_grant", "authorization code already used")
    if row.expires_at < _now():
        raise OAuthError("invalid_grant", "authorization code expired")
    if row.redirect_uri != redirect_uri:
        raise OAuthError("invalid_grant", "redirect_uri mismatch")
    if not code_verifier or not tokens.verify_pkce(code_verifier, row.code_challenge):
        raise OAuthError("invalid_grant", "PKCE verification failed")

    grant = (
        await db.execute(select(OAuthGrant).where(OAuthGrant.id == row.grant_id))
    ).scalar_one()
    if grant.revoked_at is not None:
        raise OAuthError("invalid_grant", "grant revoked")
    if grant.client_id != client_id:
        raise OAuthError("invalid_client", "client mismatch")

    row.used_at = _now()
    scopes = row.scopes.split() if row.scopes else []
    result = await _issue_tokens(db, grant, scopes, family_id=uuid.uuid4())
    await db.commit()
    return result


async def _revoke_family(db: AsyncSession, family_id, grant_id) -> None:
    now = _now()
    await db.execute(
        update(OAuthRefreshToken)
        .where(OAuthRefreshToken.family_id == family_id)
        .values(revoked_at=now)
    )
    await db.execute(
        update(OAuthGrant).where(OAuthGrant.id == grant_id).values(revoked_at=now)
    )


async def rotate_refresh_token(
    db: AsyncSession, *, refresh_token: str, client_id: str
) -> dict:
    row = (
        await db.execute(
            select(OAuthRefreshToken).where(
                OAuthRefreshToken.token_hash == tokens.hash_refresh_token(refresh_token)
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise OAuthError("invalid_grant", "unknown refresh token")

    grant = (
        await db.execute(select(OAuthGrant).where(OAuthGrant.id == row.grant_id))
    ).scalar_one()

    # Reuse of an already-consumed token => theft. Revoke the whole family+grant.
    if row.consumed_at is not None:
        await _revoke_family(db, row.family_id, grant.id)
        await db.commit()
        raise OAuthError("invalid_grant", "refresh token reuse detected")
    if row.revoked_at is not None or grant.revoked_at is not None:
        raise OAuthError("invalid_grant", "refresh token revoked")
    if row.expires_at < _now():
        raise OAuthError("invalid_grant", "refresh token expired")
    if grant.client_id != client_id:
        raise OAuthError("invalid_client", "client mismatch")

    row.consumed_at = _now()
    scopes = row.scopes.split() if row.scopes else []
    result = await _issue_tokens(db, grant, scopes, family_id=row.family_id)
    await db.commit()
    return result


async def revoke_grant(db: AsyncSession, grant_id) -> None:
    """Revoke a grant and every refresh token issued under it (user-driven
    'disconnect this app'). Access tokens die within their short TTL."""
    now = _now()
    await db.execute(
        update(OAuthGrant).where(OAuthGrant.id == grant_id).values(revoked_at=now)
    )
    await db.execute(
        update(OAuthRefreshToken)
        .where(OAuthRefreshToken.grant_id == grant_id)
        .values(revoked_at=now)
    )
    await db.commit()

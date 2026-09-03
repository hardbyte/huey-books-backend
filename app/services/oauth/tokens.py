"""Minting of OAuth access tokens, PKCE verification, refresh-token hashing.

Access tokens are the per-school API credential: an RS256 JWT the resource
server (MCP) and this API both verify via the shared ``verify.verify_token``.
Authority is bound at consent (school_id + scopes come from the grant, never
from a tool argument).
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import secrets
import uuid

from jose import jwt

from app.config import get_settings
from app.services.oauth import keys, verify

TOKEN_TYPE = "oauth"

# Scopes shown on the consent screen and mapped to RBAC when the token is used.
SCOPE_CATALOGUE_READ = "catalogue:read"
SCOPE_RECOMMENDATIONS_READ = "recommendations:read"
SCOPE_BOOKS_IMPORT = "books:import"
SCOPE_BOOKS_LABEL = "books:label"
SCOPE_OFFLINE_ACCESS = "offline_access"

SUPPORTED_SCOPES = [
    SCOPE_CATALOGUE_READ,
    SCOPE_RECOMMENDATIONS_READ,
    SCOPE_BOOKS_IMPORT,
    SCOPE_BOOKS_LABEL,
    SCOPE_OFFLINE_ACCESS,
]


def mint_access_token(
    *,
    user_id: str,
    school_id: str,
    scopes: list[str],
    grant_id: str,
    ttl_seconds: int | None = None,
) -> str:
    """Sign a per-school RS256 access token for the OAuth (MCP) path."""
    settings = get_settings()
    private_pem, kid = keys.signing_key()
    now = datetime.datetime.now(datetime.UTC)
    ttl = (
        ttl_seconds
        if ttl_seconds is not None
        else settings.OAUTH_ACCESS_TOKEN_TTL_SECONDS
    )
    claims = {
        "iss": settings.OAUTH_ISSUER,
        "aud": settings.OAUTH_API_AUDIENCE,
        "sub": str(user_id),
        "iat": now,
        "exp": now + datetime.timedelta(seconds=ttl),
        "jti": uuid.uuid4().hex,
        "typ": TOKEN_TYPE,
        "azp": "mcp-proxy",
        "school_id": str(school_id),
        "scope": " ".join(scopes),
        "grant_id": str(grant_id),
    }
    return jwt.encode(claims, private_pem, algorithm="RS256", headers={"kid": kid})


def decode_access_token(token: str) -> dict:
    """Verify an OAuth access token (typ enforced)."""
    return verify.verify_token(token, require_typ=TOKEN_TYPE)


# --- PKCE (RFC 7636, S256 only) ------------------------------------------- #
def _b64url_no_pad(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def verify_pkce(code_verifier: str, code_challenge: str) -> bool:
    """True iff BASE64URL(SHA256(code_verifier)) == code_challenge (S256)."""
    expected = _b64url_no_pad(hashlib.sha256(code_verifier.encode("ascii")).digest())
    return secrets.compare_digest(expected, code_challenge)


# --- Refresh tokens -------------------------------------------------------- #
def new_refresh_token() -> str:
    """An opaque high-entropy refresh token (stored only as a hash)."""
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    """SHA-256 hex of a refresh token — what is persisted, so a DB leak does not
    expose usable tokens."""
    return hashlib.sha256(token.encode("ascii")).hexdigest()

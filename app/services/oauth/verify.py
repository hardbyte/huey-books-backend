"""Generic, kid-dispatched token verification.

One verifier for every token the service issues, now and after the eventual
HS256->RS256 migration. The migration then becomes an issuance/config change,
not a rewrite of verification.

Security invariants:
  * Verification key + algorithm are chosen by the token's ``kid`` header, never
    by the ``alg`` in the header. Each key allows exactly ONE algorithm. This
    defeats algorithm-confusion attacks (e.g. an RS256 public key replayed as an
    HS256 secret) that python-jose does not otherwise prevent.
  * Legacy tokens (signed before this work) carry no ``kid`` and map to
    ``legacy-hs256`` — the current shared HS256 secret.
  * The JWKS endpoint publishes only RS256 public keys; the HS secret is never
    exposed.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Optional

from jose import jwt
from jose.exceptions import JWTError

from app.config import get_settings
from app.services.oauth import keys

LEGACY_KID = "legacy-hs256"
CLOCK_LEEWAY_SECONDS = 60


class TokenError(Exception):
    """Verification failed — malformed, wrong key, expired, wrong aud/iss/typ."""


@dataclass(frozen=True)
class VerifyKey:
    kid: str
    algorithm: str  # exactly one
    key: Any  # HS: secret str; RS: a JWKS dict
    issuer: Optional[str]  # expected iss, or None to skip (legacy)
    audience: Optional[str]  # expected aud, or None to skip (legacy)
    purpose: str  # "legacy" | "oauth"


@lru_cache(maxsize=1)
def _registry() -> dict[str, VerifyKey]:
    settings = get_settings()
    rs_kid = keys.signing_key()[1]
    return {
        LEGACY_KID: VerifyKey(
            kid=LEGACY_KID,
            algorithm="HS256",
            key=settings.SECRET_KEY,
            issuer=None,
            audience=None,
            purpose="legacy",
        ),
        rs_kid: VerifyKey(
            kid=rs_kid,
            algorithm="RS256",
            key={"keys": [keys.public_jwk()]},
            issuer=settings.OAUTH_ISSUER,
            audience=settings.OAUTH_API_AUDIENCE,
            purpose="oauth",
        ),
    }


def verify_token(token: str, *, require_typ: Optional[str] = None) -> dict:
    """Verify a JWT and return its claims. Raises TokenError on any failure."""
    try:
        header = jwt.get_unverified_header(token)
    except JWTError as exc:
        raise TokenError(f"malformed token header: {exc}") from exc

    kid = header.get("kid", LEGACY_KID)
    vkey = _registry().get(kid)
    if vkey is None:
        raise TokenError(f"unknown key id: {kid!r}")

    options = {
        "leeway": CLOCK_LEEWAY_SECONDS,
        "verify_aud": vkey.audience is not None,
    }
    try:
        claims = jwt.decode(
            token,
            vkey.key,
            algorithms=[vkey.algorithm],  # single alg — no header-driven choice
            audience=vkey.audience,
            issuer=vkey.issuer,
            options=options,
        )
    except JWTError as exc:
        raise TokenError(str(exc)) from exc

    if require_typ is not None and claims.get("typ") != require_typ:
        raise TokenError(
            f"wrong token type: expected {require_typ!r}, got {claims.get('typ')!r}"
        )
    return claims

"""RS256 signing key + JWKS for OAuth-issued access tokens.

The resource server (the MCP server) verifies tokens against the JWKS published
here, so it never holds the signing key. In production the key comes from
``OAUTH_PRIVATE_KEY_PEM`` (Secret Manager); with no key configured an ephemeral
one is generated per process — acceptable for local/tests, not for prod (tokens
would not survive a restart or validate across instances).
"""

from __future__ import annotations

import base64
import hashlib
import json
from functools import lru_cache

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwk

from app.config import get_settings


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _jwk_thumbprint(public_jwk: dict) -> str:
    """RFC 7638 thumbprint — a stable ``kid`` derived from the key itself."""
    canonical = json.dumps(
        {"e": public_jwk["e"], "kty": public_jwk["kty"], "n": public_jwk["n"]},
        separators=(",", ":"),
        sort_keys=True,
    )
    return _b64url(hashlib.sha256(canonical.encode()).digest())


def _jwk_from_public_pem(public_pem: str) -> dict:
    public_jwk = jwk.construct(public_pem, "RS256").to_dict()
    # jose returns bytes for n/e on some backends; normalise to str.
    public_jwk = {
        k: (v.decode() if isinstance(v, bytes) else v) for k, v in public_jwk.items()
    }
    public_jwk.update(
        {"kid": _jwk_thumbprint(public_jwk), "alg": "RS256", "use": "sig"}
    )
    return public_jwk


@lru_cache(maxsize=1)
def _keypair() -> tuple[str, dict, str]:
    """Return (private_pem, public_jwk, kid). Cached for the process lifetime."""
    settings = get_settings()
    pem = settings.OAUTH_PRIVATE_KEY_PEM.strip()
    if pem:
        private_key = serialization.load_pem_private_key(pem.encode(), password=None)
    else:
        if not settings.OAUTH_ALLOW_EPHEMERAL_KEY:
            raise RuntimeError(
                "OAUTH_PRIVATE_KEY_PEM must be set (OAUTH_ALLOW_EPHEMERAL_KEY is off): "
                "an ephemeral key breaks token verification across instances."
            )
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()

    public_pem = (
        private_key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    public_jwk = _jwk_from_public_pem(public_pem)
    return pem, public_jwk, public_jwk["kid"]


@lru_cache(maxsize=1)
def previous_public_jwks() -> list[dict]:
    """Retired public keys still accepted during a rotation window."""
    settings = get_settings()
    return [
        _jwk_from_public_pem(pem)
        for pem in settings.OAUTH_PREVIOUS_PUBLIC_KEYS_PEM
        if pem.strip()
    ]


def signing_key() -> tuple[str, str]:
    """(private_pem, kid) for signing."""
    pem, _, kid = _keypair()
    return pem, kid


def public_jwk() -> dict:
    return _keypair()[1]


def jwks() -> dict:
    """The JWKS document served at the JWKS endpoint (current + retired keys)."""
    return {"keys": [public_jwk(), *previous_public_jwks()]}

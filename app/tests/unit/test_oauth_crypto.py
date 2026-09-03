"""Unit tests for the OAuth crypto/token core (no DB).

Covers JWKS shape, RS256 mint/verify, audience/type enforcement, the legacy
HS256 path, PKCE, refresh hashing, and — most importantly — that verification
dispatches on ``kid`` (one algorithm per key) so an algorithm-confusion attack
(RS256 public key replayed as an HS256 secret) is rejected.
"""

import datetime
import hashlib
import json

import pytest
from jose import jwt

from app.config import get_settings
from app.services.oauth import keys, tokens, verify


def _mint(**over):
    kwargs = dict(
        user_id="user-1",
        school_id="school-A",
        scopes=[tokens.SCOPE_CATALOGUE_READ, tokens.SCOPE_BOOKS_LABEL],
        grant_id="grant-1",
    )
    kwargs.update(over)
    return tokens.mint_access_token(**kwargs)


def test_jwks_is_public_only_and_well_formed():
    doc = keys.jwks()
    assert doc["keys"], "JWKS has at least one key"
    (jwk,) = doc["keys"]
    assert jwk["kty"] == "RSA"
    assert jwk["alg"] == "RS256"
    assert jwk["use"] == "sig"
    assert jwk["kid"]
    # No private material must ever be published.
    assert "d" not in jwk and "p" not in jwk and "q" not in jwk


def test_mint_and_verify_roundtrip():
    token = _mint()
    claims = tokens.decode_access_token(token)
    settings = get_settings()
    assert claims["sub"] == "user-1"
    assert claims["school_id"] == "school-A"
    assert claims["grant_id"] == "grant-1"
    assert claims["scope"] == "catalogue:read books:label"
    assert claims["aud"] == settings.OAUTH_API_AUDIENCE
    assert claims["iss"] == settings.OAUTH_ISSUER
    assert claims["typ"] == "oauth"


def test_wrong_audience_rejected():
    settings = get_settings()
    private_pem, kid = keys.signing_key()
    now = datetime.datetime.now(datetime.UTC)
    bad = jwt.encode(
        {
            "iss": settings.OAUTH_ISSUER,
            "aud": "https://evil.example",
            "sub": "u",
            "iat": now,
            "exp": now + datetime.timedelta(minutes=5),
            "typ": "oauth",
        },
        private_pem,
        algorithm="RS256",
        headers={"kid": kid},
    )
    with pytest.raises(verify.TokenError):
        tokens.decode_access_token(bad)


def test_require_typ_enforced():
    # A legacy HS256 token (no kid, no typ) must not pass as an oauth token.
    settings = get_settings()
    legacy = jwt.encode({"sub": "u"}, settings.SECRET_KEY, algorithm="HS256")
    assert verify.verify_token(legacy)["sub"] == "u"  # legacy path works
    with pytest.raises(verify.TokenError):
        verify.verify_token(legacy, require_typ="oauth")


def test_algorithm_confusion_rejected():
    """Forge an HS256 token using the RS256 PUBLIC key as the shared secret and
    the RS key's kid. Because verification is pinned to RS256 for that kid, it
    must be rejected — this is the classic alg-confusion attack."""
    _, kid = keys.signing_key()
    public_jwk_material = json.dumps(keys.public_jwk(), sort_keys=True)
    forged = jwt.encode(
        {"sub": "attacker", "typ": "oauth"},
        public_jwk_material,  # attacker only has the PUBLIC key
        algorithm="HS256",
        headers={"kid": kid},
    )
    with pytest.raises(verify.TokenError):
        verify.verify_token(forged)


def test_legacy_hs256_cannot_forge_oauth_typ():
    """A holder of the legacy HS256 secret must not be able to mint a token that
    the OAuth-aware path accepts, even by setting typ='oauth' (H3)."""
    settings = get_settings()
    forged = jwt.encode(
        {"sub": "u", "typ": "oauth", "school_id": "any", "scope": "books:label"},
        settings.SECRET_KEY,
        algorithm="HS256",
    )
    # It verifies as a legacy token, but not as an oauth token.
    assert verify.verify_token(forged)["sub"] == "u"
    with pytest.raises(verify.TokenError):
        tokens.decode_access_token(forged)


def test_kid_must_be_string():
    settings = get_settings()
    tok = jwt.encode({"sub": "u"}, settings.SECRET_KEY, algorithm="HS256", headers={"kid": ["x"]})
    with pytest.raises(verify.TokenError):
        verify.verify_token(tok)


def test_unknown_kid_rejected():
    settings = get_settings()
    tok = jwt.encode({"sub": "u"}, settings.SECRET_KEY, algorithm="HS256", headers={"kid": "nope"})
    with pytest.raises(verify.TokenError):
        verify.verify_token(tok)


def test_pkce_s256():
    verifier = "a" * 64
    challenge = tokens._b64url_no_pad(hashlib.sha256(verifier.encode()).digest())
    assert tokens.verify_pkce(verifier, challenge) is True
    assert tokens.verify_pkce(verifier, "wrong") is False


def test_refresh_token_hashing():
    t1 = tokens.new_refresh_token()
    t2 = tokens.new_refresh_token()
    assert t1 != t2
    assert tokens.hash_refresh_token(t1) == tokens.hash_refresh_token(t1)
    assert tokens.hash_refresh_token(t1) != tokens.hash_refresh_token(t2)
    assert tokens.hash_refresh_token(t1) != t1  # stored value is not the token

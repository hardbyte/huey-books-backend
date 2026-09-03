"""OAuth authorization-server HTTP surface.

Increment 2 adds the token endpoint (authorization_code + refresh_token grants,
PKCE, rotating refresh) alongside the JWKS endpoint. The ``/oauth/authorize``
consent+login screen (which reuses the Firebase login and a school picker) is a
separate sub-task; it calls ``grants.create_authorization_code``.

The client-facing OAuth surface (protected-resource metadata, DCR, consent) is
served by the MCP server via FastMCP's OAuthProxy; this backend is only the
proxy's trusted upstream authorization server.
"""

import base64
import binascii
import secrets
from urllib.parse import unquote

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse

from app.api.dependencies.async_db_dep import DBSessionDep
from app.config import get_settings
from app.services.oauth import grants, keys
from app.services.oauth.grants import OAuthError

# Root-mounted (NOT under /v1): JWKS lives at the origin's /.well-known path.
well_known_router = APIRouter(tags=["OAuth"])


@well_known_router.get("/.well-known/jwks.json")
def jwks() -> JSONResponse:
    """Public keys for verifying RS256 access tokens issued by this service."""
    return JSONResponse(keys.jwks(), headers={"Cache-Control": "public, max-age=300"})


# Mounted under the API prefix; the proxy is configured with the absolute URL.
token_router = APIRouter(prefix="/oauth", tags=["OAuth"])

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
_ERROR_STATUS = {"invalid_client": 401}


def _error(
    error: str, description: str = "", status_code: int | None = None
) -> JSONResponse:
    return JSONResponse(
        {"error": error, "error_description": description},
        status_code=status_code or _ERROR_STATUS.get(error, 400),
        headers=_NO_STORE,
    )


def _basic_auth(request: Request) -> tuple[str, str] | None:
    """Parse client_secret_basic credentials (FastMCP's proxy default)."""
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("basic "):
        return None
    try:
        decoded = base64.b64decode(header[6:]).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None
    client_id, sep, client_secret = decoded.partition(":")
    if not sep:
        return None
    return unquote(client_id), unquote(client_secret)


@token_router.post("/token")
async def oauth_token(
    request: Request,
    db: DBSessionDep,
    grant_type: str = Form(...),
    client_id: str | None = Form(None),
    client_secret: str | None = Form(None),
    code: str | None = Form(None),
    redirect_uri: str | None = Form(None),
    code_verifier: str | None = Form(None),
    refresh_token: str | None = Form(None),
) -> JSONResponse:
    """OAuth 2.1 token endpoint for the MCP proxy (confidential client)."""
    settings = get_settings()

    # Accept client_secret_basic (proxy default) or client_secret_post; reject a
    # conflicting mix.
    basic = _basic_auth(request)
    if basic is not None:
        if client_id is not None and (client_id, client_secret) != basic:
            return _error("invalid_request", "conflicting client credentials")
        client_id, client_secret = basic

    expected_secret = settings.OAUTH_MCP_CLIENT_SECRET
    client_ok = (
        client_id == settings.OAUTH_MCP_CLIENT_ID
        and bool(expected_secret)
        and bool(client_secret)
        and secrets.compare_digest(client_secret, expected_secret)
    )
    if not client_ok:
        resp = _error("invalid_client", "client authentication failed")
        if basic is not None:
            resp.headers["WWW-Authenticate"] = 'Basic realm="oauth"'
        return resp

    try:
        if grant_type == "authorization_code":
            if not (code and redirect_uri and code_verifier):
                raise OAuthError(
                    "invalid_request",
                    "code, redirect_uri and code_verifier are required",
                )
            result = await grants.exchange_authorization_code(
                db,
                code=code,
                redirect_uri=redirect_uri,
                code_verifier=code_verifier,
                client_id=client_id,
            )
        elif grant_type == "refresh_token":
            if not refresh_token:
                raise OAuthError("invalid_request", "refresh_token is required")
            result = await grants.rotate_refresh_token(
                db, refresh_token=refresh_token, client_id=client_id
            )
        else:
            raise OAuthError("unsupported_grant_type", grant_type)
    except OAuthError as exc:
        return _error(exc.error, exc.description)

    return JSONResponse(result, headers=_NO_STORE)

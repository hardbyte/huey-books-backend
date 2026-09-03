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
import uuid
from urllib.parse import quote, unquote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select

from app.api.dependencies.async_db_dep import DBSessionDep
from app.api.dependencies.security import get_current_active_user
from app.config import get_settings
from app.models.school import School
from app.models.user import User
from app.services.oauth import grants, keys, tokens
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


# The consent step: the admin-UI authorize page (where the librarian is logged in
# via Firebase) POSTs here with the chosen school + scopes; this mints the code
# and returns where the browser should be redirected (the proxy's callback).
authorize_router = APIRouter(prefix="/oauth", tags=["OAuth"])


class OAuthConsentIn(BaseModel):
    client_id: str
    redirect_uri: str
    scope: str
    school_id: uuid.UUID  # the school's wriveted_identifier
    code_challenge: str
    code_challenge_method: str = "S256"
    state: str | None = None


def _redirect_uri_allowed(redirect_uri: str, allowed: list[str]) -> bool:
    if redirect_uri in allowed:
        return True
    # Loopback for local development (any port).
    return redirect_uri.startswith(("http://localhost", "http://127.0.0.1"))


@authorize_router.post("/authorize")
async def oauth_authorize(
    body: OAuthConsentIn,
    db: DBSessionDep,
    current_user: User = Depends(get_current_active_user),
) -> dict:
    """Consent submission — mint an authorization code for a school the librarian
    is a member of. Requires the librarian's own (Firebase-exchanged) session."""
    settings = get_settings()
    if body.client_id != settings.OAUTH_MCP_CLIENT_ID:
        raise HTTPException(status_code=400, detail="Unknown client")
    if not _redirect_uri_allowed(
        body.redirect_uri, settings.OAUTH_ALLOWED_REDIRECT_URIS
    ):
        raise HTTPException(status_code=400, detail="redirect_uri not allowed")
    if body.code_challenge_method != "S256" or not body.code_challenge:
        raise HTTPException(status_code=400, detail="PKCE S256 challenge required")
    requested = body.scope.split()
    if not set(requested) <= set(tokens.SUPPORTED_SCOPES):
        raise HTTPException(status_code=400, detail="Unsupported scope requested")

    school_int = (
        await db.execute(
            select(School.id).where(School.wriveted_identifier == body.school_id)
        )
    ).scalar_one_or_none()
    if school_int is None:
        raise HTTPException(status_code=404, detail="School not found")
    principals = set(await current_user.get_principals())
    is_wriveted_admin = "role:admin" in principals
    if not is_wriveted_admin and (
        f"schooladmin:{school_int}" not in principals
        and f"educator:{school_int}" not in principals
    ):
        raise HTTPException(status_code=403, detail="Not a member of this school")

    code = await grants.create_authorization_code(
        db,
        user_id=str(current_user.id),
        school_id=str(body.school_id),
        client_id=body.client_id,
        redirect_uri=body.redirect_uri,
        scopes=requested,
        code_challenge=body.code_challenge,
        code_challenge_method=body.code_challenge_method,
    )
    sep = "&" if "?" in body.redirect_uri else "?"
    url = f"{body.redirect_uri}{sep}code={quote(code)}"
    if body.state:
        url += f"&state={quote(body.state)}"
    return {"redirect_url": url}

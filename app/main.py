import textwrap

from fastapi import FastAPI
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from starlette import status
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse
from structlog import get_logger

from app.api.analytics import router as analytics_router
from app.api.external_api_router import api_router
from app.api.oauth import well_known_router as oauth_well_known_router
from app.config import get_settings
from app.events import lifespan
from app.logging import init_logging, init_tracing

api_docs = textwrap.dedent(
    """
# 🤖 

Welcome human to a brief outline of the Wriveted API. 

Use this API to add, edit, and remove information about Users, Books, Schools
and Libraries.

The API is designed for use by multiple users:
- **Library Management Systems**. In particular see the section on 
  updating and setting Schools collections.
- **Wriveted Staff** either directly via scripts or via an admin UI.
- **Huey** chatbot

Note all requests require credentials, with the exceptions of getting public information on 
schools, the application version, and the security policy.

## 🔐 Authentication

The good news is that as an API user you just need to send an access token
in the `Authorization` header and all endpoints should *just work*. The
notable exception being the `/auth/firebase` endpoint which exchanges a firebase
SSO token for a Wriveted API Access Token.

As a LMS integrator or developer your access token will be provided to you by the 
Wriveted team.

You can check it by calling the `GET /auth/me` endpoint.

## 🚨 Authorization

The API implements role based access control, only particular roles are allowed
to add new schools or edit collections.

"""
)

settings = get_settings()
init_logging(settings)
logger = get_logger()
logger.info("Starting Wriveted API")

# The mounted MCP needs its own session-manager lifespan entered too.
if settings.MCP_ENABLED:
    from contextlib import asynccontextmanager

    from app.mcp.server import http_app as mcp_http_app

    _api_lifespan = lifespan

    @asynccontextmanager
    async def lifespan(app):  # noqa: F811
        async with _api_lifespan(app):
            async with mcp_http_app.lifespan(app):
                yield


app = FastAPI(
    title="Wriveted API",
    description=api_docs,
    docs_url="/v1/docs",
    redoc_url="/v1/redoc",
    debug=settings.DEBUG,
    lifespan=lifespan,
    # version=metadata.version("wriveted-api"),
)

if settings.MCP_ENABLED:
    # RFC 9728 PRM lives at the origin root, but the sub-app only serves it under
    # /mcp; alias it (and the RFC 8414 AS-metadata root form) here.
    _mcp_base = settings.MCP_BASE_URL.rstrip("/")

    @app.get("/.well-known/oauth-protected-resource/mcp")
    @app.get("/.well-known/oauth-protected-resource/mcp/")
    async def mcp_protected_resource_metadata():
        return {
            # No trailing slash: must equal the URL the client dialed.
            "resource": _mcp_base,
            "authorization_servers": [_mcp_base],
            "scopes_supported": [],
            "bearer_methods_supported": ["header"],
        }

    @app.get("/.well-known/oauth-authorization-server/mcp")
    async def mcp_as_metadata_alias():
        return RedirectResponse(f"{_mcp_base}/.well-known/oauth-authorization-server")

    app.mount("/mcp", mcp_http_app)

init_tracing(app, settings)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    logger.warning(
        f"The client sent invalid data!: {exc}\n\n{exc.errors()}",
        request=request.url,
    )
    return await request_validation_exception_handler(request, exc)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Log the full traceback for any unhandled exception before returning a 500.

    Without this, Cloud Run only records the bare 500 status with no stack
    trace, making production failures effectively undebuggable.
    """
    logger.error(
        "Unhandled exception",
        error=str(exc),
        path=request.url.path,
        method=request.method,
        exc_info=True,
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error"},
    )


# Set all CORS enabled origins
if settings.BACKEND_CORS_ORIGINS:
    logger.info(
        "Enabling cross origin restrictions",
        cors_origins=[str(c) for c in settings.BACKEND_CORS_ORIGINS],
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[str(origin) for origin in settings.BACKEND_CORS_ORIGINS],
        # allow preview channels from library dashboard app frontend
        allow_origin_regex=r"(https://wriveted.*web\.app)|(https://huey-books.*web\.app)",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )


# @app.middleware("http")
# async def request_middleware(request: Request, call_next):
#     """
#     Middleware to add a UUID to each request.
#
#     Adds a header to the response `X-Request-ID` with this ID.
#     """
#     request_id = str(uuid.uuid4())
#     clear_contextvars()
#     bind_contextvars(request_id=request_id, request_path=request.url.path)
#
#     logger.debug("Request started", request_method=request.method)
#
#     response = await call_next(request)
#     response.headers["X-Request-ID"] = request_id
#     logger.debug("Request ended")
#     return response


app.include_router(api_router, prefix=settings.API_V1_STR)
app.include_router(
    analytics_router, prefix=f"{settings.API_V1_STR}/cms", tags=["analytics"]
)
app.include_router(oauth_well_known_router)  # root-mounted: /.well-known/jwks.json


@app.get("/")
async def root():
    """
    Redirects to the OpenAPI documentation for the current version
    """
    return RedirectResponse("/v1/docs", status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@app.get("/docs")
async def redirect_old_docs_route():
    """
    Redirects to the OpenAPI documentation for the current version
    """
    return RedirectResponse("/v1/docs", status_code=status.HTTP_307_TEMPORARY_REDIRECT)

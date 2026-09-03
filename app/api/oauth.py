"""OAuth authorization-server HTTP surface.

Increment 1 exposes only the JWKS endpoint — enough for a resource server (the
MCP OAuth proxy's ``JWTVerifier``) to verify the RS256 access tokens this service
issues. The ``/oauth/authorize`` + ``/oauth/token`` flow and the grants store land
in the next increment.

The client-facing OAuth surface (protected-resource metadata, authorization-server
metadata, dynamic client registration, consent) is served by the MCP server via
FastMCP's OAuthProxy; this backend is only the proxy's trusted upstream.
"""

from fastapi import APIRouter

from app.services.oauth import keys

# Root-mounted (NOT under /v1): JWKS lives at the origin's /.well-known path.
well_known_router = APIRouter(tags=["OAuth"])


@well_known_router.get("/.well-known/jwks.json")
def jwks() -> dict:
    """Public keys for verifying RS256 access tokens issued by this service."""
    return keys.jwks()

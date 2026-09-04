"""Auth, session and RBAC bridge for the mounted MCP server.

MCP tools run under FastMCP's OAuthProxy, so ``get_access_token`` yields the
backend-issued RS256 API token (already signature/issuer/audience verified).
Rather than re-implement authorisation, we reconstruct the same OAuth-confined
principals ``get_active_principals`` builds for the REST API: one user, one
granted school, coarse read/write from the token scopes. An MCP action therefore
has identical least-privilege authority and cross-school access stays impossible.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import with_polymorphic

from app.db.session import get_async_session_maker
from app.models.school import School
from app.models.user import User
from app.services.oauth.authz import build_oauth_principals


@dataclass
class MCPContext:
    db: AsyncSession
    user: User
    principals: list
    scopes: set[str]
    school: School
    school_wid: str


_WRITE_SCOPES = {"books:import", "books:label"}


def require_write_scope(ctx: "MCPContext", scope: str) -> None:
    """Fail closed unless the token carries the write scope this tool needs."""
    if scope not in ctx.scopes:
        raise ToolError(
            f"This action needs the '{scope}' permission, which this connection "
            "was not granted. Re-authorise the tool with write access."
        )


def require_principal(ctx: "MCPContext", *principals: str, action: str) -> None:
    """Fail closed unless the confined principals include one of ``principals``.

    This re-applies the same RBAC the REST endpoints enforce, so an MCP write can
    never exceed the token's granted school/role (e.g. a plain educator has no
    schooladmin principal, and a removed member has none for the school at all)."""
    if not any(p in ctx.principals for p in principals):
        raise ToolError(f"You are not permitted to {action} for this school.")


@asynccontextmanager
async def mcp_context():
    """Yield the caller's DB session, user, granted school and confined principals."""
    token = get_access_token()
    claims = getattr(token, "claims", None) or {}
    uid = claims.get("uid")
    school_wid = claims.get("school_id")
    scopes = set((claims.get("scope") or "").split())
    if not uid or not school_wid:
        raise ToolError("Not authenticated: token is missing its user or school.")

    maker = get_async_session_maker()
    async with maker() as db:
        # Eager-load joined-inheritance columns (e.g. Educator.school_id) so
        # get_principals() below doesn't trigger an async lazy load (MissingGreenlet).
        user_poly = with_polymorphic(User, "*")
        user = (
            await db.execute(select(user_poly).where(user_poly.id == uid))
        ).scalar_one_or_none()
        if user is None or not user.is_active:
            raise ToolError("Not authenticated: unknown or inactive user.")
        school = (
            await db.execute(
                select(School).where(School.wriveted_identifier == school_wid)
            )
        ).scalar_one_or_none()
        if school is None:
            raise ToolError("The school this token was issued for no longer exists.")

        real = set(await user.get_principals())
        principals = build_oauth_principals(user.id, real, school.id, scopes)
        yield MCPContext(
            db=db,
            user=user,
            principals=principals,
            scopes=scopes,
            school=school,
            school_wid=school_wid,
        )

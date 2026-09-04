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


def require_scope(ctx: "MCPContext", scope: str, action: str) -> None:
    """Fail closed unless the token carries the scope this tool needs."""
    if scope not in ctx.scopes:
        raise ToolError(
            f"This connection was not granted the '{scope}' permission needed to "
            f"{action}. Re-authorise the tool with that access."
        )


def require_write_scope(ctx: "MCPContext", scope: str) -> None:
    """Fail closed unless the token carries the write scope this tool needs."""
    require_scope(ctx, scope, "make this change")


def require_principal(ctx: "MCPContext", *principals: str, action: str) -> None:
    """Fail closed unless the confined principals include one of ``principals``.

    This re-applies the same RBAC the REST endpoints enforce, so an MCP write can
    never exceed the token's granted school/role (e.g. a plain educator has no
    schooladmin principal, and a removed member has none for the school at all)."""
    if not any(p in ctx.principals for p in principals):
        raise ToolError(f"You are not permitted to {action} for this school.")


@dataclass
class MCPIdentity:
    user: User
    is_admin: bool
    authorized: set[str]  # school wids the token is confined to (non-admin)
    default_school: str | None
    scopes: set[str]


# Per-user "current school" default for use_school (a convenience; the explicit
# `school` tool argument always works). In-memory + best-effort: not shared across
# instances, so it can reset — never a security boundary (mcp_context re-validates).
_session_school: dict[str, str] = {}


def set_session_school(uid: str, wid: str) -> None:
    _session_school[uid] = wid


def get_session_school(uid: str) -> str | None:
    return _session_school.get(uid)


def _claims_schools(claims: dict) -> tuple[str | None, set[str]]:
    default = claims.get("school_id")
    schools = set(claims.get("schools") or ([default] if default else []))
    return default, schools


async def _load_user(db: AsyncSession, uid: str) -> User:
    # Eager-load joined-inheritance columns (e.g. Educator.school_id) so
    # get_principals() doesn't trigger an async lazy load (MissingGreenlet).
    user_poly = with_polymorphic(User, "*")
    user = (
        await db.execute(select(user_poly).where(user_poly.id == uid))
    ).scalar_one_or_none()
    if user is None or not user.is_active:
        raise ToolError("Not authenticated: unknown or inactive user.")
    return user


@asynccontextmanager
async def mcp_identity():
    """Yield the caller and which schools they may act for (no specific school)."""
    claims = getattr(get_access_token(), "claims", None) or {}
    uid = claims.get("uid")
    if not uid:
        raise ToolError("Not authenticated: token is missing its user.")
    default_school, authorized = _claims_schools(claims)
    async with get_async_session_maker()() as db:
        user = await _load_user(db, uid)
        is_admin = "role:admin" in set(await user.get_principals())
        yield MCPIdentity(
            user=user,
            is_admin=is_admin,
            authorized=authorized,
            default_school=default_school,
            scopes=set((claims.get("scope") or "").split()),
        )


@asynccontextmanager
async def mcp_context(requested_school: str | None = None):
    """Yield the DB session, user, resolved school and confined principals.

    School resolution: the explicit ``requested_school`` wins, then the session
    default (use_school), then the token's default school. A Wriveted admin may
    act for ANY school by identifier; everyone else is confined to the schools the
    token was granted, and must still be a live member of the resolved school."""
    token = get_access_token()
    claims = getattr(token, "claims", None) or {}
    uid = claims.get("uid")
    scopes = set((claims.get("scope") or "").split())
    default_wid, authorized = _claims_schools(claims)
    if not uid:
        raise ToolError("Not authenticated: token is missing its user.")

    maker = get_async_session_maker()
    async with maker() as db:
        user = await _load_user(db, uid)
        real = set(await user.get_principals())
        is_admin = "role:admin" in real

        target = requested_school or _session_school.get(uid) or default_wid
        if not target:
            raise ToolError(
                "No school selected. Pass `school` (a school identifier) or call "
                "use_school first."
            )
        if not is_admin and target not in authorized:
            raise ToolError(
                "This connection is not authorised for that school. Call "
                "list_my_schools to see the schools you can act for."
            )
        school = (
            await db.execute(select(School).where(School.wriveted_identifier == target))
        ).scalar_one_or_none()
        if school is None:
            raise ToolError(f"Unknown school: {target}.")

        principals = build_oauth_principals(user.id, real, school.id, scopes)
        # Live membership: a token outlives its grant (refresh up to the absolute
        # TTL), so re-check the user still belongs to the resolved school on every
        # call. build_oauth_principals only carries the school principals for a
        # current member (or a Wriveted admin); an offboarded user has neither.
        if (
            f"educator:{school.id}" not in principals
            and f"schooladmin:{school.id}" not in principals
        ):
            raise ToolError(
                "You no longer have access to this school. Reconnect the tool and "
                "choose a school you are staff at."
            )
        yield MCPContext(
            db=db,
            user=user,
            principals=principals,
            scopes=scopes,
            school=school,
            school_wid=target,
        )

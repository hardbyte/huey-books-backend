"""Structured logging at the MCP tool boundary: who called what, for which
school, how long it took, and whether it succeeded."""

from __future__ import annotations

import time

import structlog
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import Middleware

logger = structlog.get_logger("mcp")


def _caller() -> dict:
    try:
        claims = getattr(get_access_token(), "claims", None) or {}
        return {"uid": claims.get("uid"), "school": claims.get("school_id")}
    except Exception:
        return {}


def _arg_summary(arguments: dict | None) -> dict:
    """Argument shapes without dumping large/sensitive values (e.g. ISBN lists)."""
    if not arguments:
        return {}
    return {
        k: (f"[{len(v)} items]" if isinstance(v, (list, tuple)) else v)
        for k, v in arguments.items()
    }


class ToolCallLogger(Middleware):
    async def on_call_tool(self, context, call_next):
        log = logger.bind(
            tool=getattr(context.message, "name", "?"),
            args=_arg_summary(getattr(context.message, "arguments", None)),
            **_caller(),
        )
        start = time.monotonic()
        try:
            result = await call_next(context)
        except Exception as exc:
            log.warning(
                "mcp_tool_call_failed",
                ms=round((time.monotonic() - start) * 1000),
                error=str(exc),
            )
            raise
        log.info("mcp_tool_call", ms=round((time.monotonic() - start) * 1000))
        return result

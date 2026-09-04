"""Trace-style logging at the MCP tool boundary: bind the caller, school and a
per-call id into structlog's contextvars so every downstream log line (services,
repositories, the MV refresh, …) is correlated to the tool call that caused it."""

from __future__ import annotations

import time
import uuid

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
        tool = getattr(context.message, "name", "?")
        start = time.monotonic()
        # Bound for the duration of the call, so downstream logs inherit it (and
        # it propagates into run_in_threadpool, which copies the context).
        with structlog.contextvars.bound_contextvars(
            mcp_tool=tool,
            mcp_call_id=uuid.uuid4().hex[:12],
            **_caller(),
        ):
            try:
                result = await call_next(context)
            except Exception as exc:
                logger.warning(
                    "mcp_tool_call_failed",
                    ms=round((time.monotonic() - start) * 1000),
                    error=str(exc),
                )
                raise
            logger.info(
                "mcp_tool_call",
                ms=round((time.monotonic() - start) * 1000),
                args=_arg_summary(getattr(context.message, "arguments", None)),
            )
            return result

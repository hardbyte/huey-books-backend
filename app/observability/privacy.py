import re
import sys
import traceback
from collections.abc import Mapping
from contextvars import ContextVar
from typing import Any

request_secrets: ContextVar[tuple[str, ...]] = ContextVar("request_secrets", default=())
_SESSION_PATH = re.compile(r"(/chat/sessions/)[^/\s?\"'<>]+")
_URL_QUERY = re.compile(r"(https?://[^\s?\"'<>]+)\?[^\s\"'<>]*")
_PRIVATE_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "set-cookie",
        "session_token",
        "csrf_token",
        "x-chat-session",
        "x-csrf-token",
        "x-observation-receipt",
        "x-response-timing-token",
        "observation_receipt",
        "user_input",
        "session_state",
        "full_state",
        "state_updates",
        "initial_state",
    }
)
_NORMALIZED_PRIVATE_KEYS = {name.replace("_", "-") for name in _PRIVATE_KEYS}


def redact(value: Any, key: str = "") -> Any:
    normalized_key = key.lower().replace("_", "-").rsplit(".", 1)[-1]
    if normalized_key in _NORMALIZED_PRIVATE_KEYS:
        return "[redacted]"
    if isinstance(value, str):
        value = _SESSION_PATH.sub(r"\1{session_token}", value)
        value = _URL_QUERY.sub(r"\1?[redacted]", value)
        for secret in request_secrets.get():
            if secret:
                value = value.replace(secret, "[redacted]")
        return value
    if isinstance(value, Mapping):
        return {name: redact(item, str(name)) for name, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


def redact_log_event(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    return redact(event_dict)


def is_database_exception_type(type_name: str) -> bool:
    return isinstance(type_name, str) and type_name.startswith(
        ("sqlalchemy.exc.", "asyncpg.exceptions.", "psycopg2.")
    )


def redact_database_exception(
    _: Any, __: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    exception_info = event_dict.get("exc_info")
    if exception_info is True:
        exception_info = sys.exc_info()
    elif isinstance(exception_info, BaseException):
        exception_info = (
            type(exception_info),
            exception_info,
            exception_info.__traceback__,
        )
    if not isinstance(exception_info, tuple) or len(exception_info) != 3:
        return event_dict
    exception = exception_info[1]
    if exception is None:
        return event_dict
    type_name = f"{type(exception).__module__}.{type(exception).__name__}"
    if not is_database_exception_type(type_name):
        return event_dict
    event_dict.pop("exc_info", None)
    original = getattr(exception, "orig", exception)
    for value in (str(exception), str(original)):
        if value:
            for key, field in event_dict.items():
                if isinstance(field, str):
                    event_dict[key] = field.replace(value, "Database operation failed")
    event_dict["exception"] = (
        "".join(traceback.format_tb(exception_info[2]))
        + f"{type_name}: Database operation failed"
    )
    sqlstate = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    if isinstance(sqlstate, str) and re.fullmatch(r"[A-Z0-9]{5}", sqlstate):
        event_dict["sqlstate"] = sqlstate
    return event_dict

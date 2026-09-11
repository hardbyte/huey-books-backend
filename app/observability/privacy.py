import re
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
        "x-response-timing-token",
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

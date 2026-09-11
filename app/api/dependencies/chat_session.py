import secrets
from typing import Annotated

from fastapi import Header, HTTPException, Path, Request


def legacy_session_token(
    session_token: Annotated[str, Path(min_length=1, max_length=128)],
) -> str:
    return session_token


def get_chat_session_token(
    request: Request,
    token_header: Annotated[
        str | None, Header(alias="X-Chat-Session", max_length=128)
    ] = None,
) -> str:
    path_token = request.path_params.get("session_token")
    if any(token and not token.isascii() for token in (path_token, token_header)):
        raise HTTPException(status_code=401, detail="Invalid chat session credential")
    if (
        path_token
        and token_header
        and not secrets.compare_digest(path_token, token_header)
    ):
        raise HTTPException(status_code=400, detail="Conflicting session credentials")
    token = token_header or path_token
    if not token or len(token) > 128:
        raise HTTPException(status_code=401, detail="Chat session header required")
    return token

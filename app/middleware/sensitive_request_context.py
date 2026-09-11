import re

from starlette.types import ASGIApp, Receive, Scope, Send

from app.observability.privacy import request_secrets


class SensitiveRequestContextMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        secrets = [
            headers[key].decode("latin1")
            for key in (
                b"authorization",
                b"x-chat-session",
                b"x-csrf-token",
                b"x-response-timing-token",
            )
            if key in headers
        ]
        if authorization := headers.get(b"authorization"):
            scheme, separator, credential = authorization.decode("latin1").partition(
                " "
            )
            if separator and scheme.lower() == "bearer":
                secrets.append(credential)
        if match := re.search(r"/chat/sessions/([^/]+)", scope.get("path", "")):
            secrets.append(match[1])
        context = request_secrets.set(tuple(secrets))
        try:
            await self.app(scope, receive, send)
        finally:
            request_secrets.reset(context)

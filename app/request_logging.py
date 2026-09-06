from time import perf_counter
from uuid import uuid4

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = structlog.get_logger()


class RequestLoggingMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = str(uuid4())
        started = perf_counter()
        status_code = 500
        response_ms = None

        async def correlated_send(message: Message) -> None:
            nonlocal status_code, response_ms
            if message["type"] == "http.response.start":
                status_code = message["status"]
                message = dict(message)
                message["headers"] = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() != b"x-request-id"
                ] + [(b"x-request-id", request_id.encode("ascii"))]
            await send(message)
            if message["type"] == "http.response.body" and not message.get(
                "more_body", False
            ):
                response_ms = (perf_counter() - started) * 1000

        with structlog.contextvars.bound_contextvars(request_id=request_id):
            try:
                await self.app(scope, receive, correlated_send)
            finally:
                logger.info(
                    "HTTP request completed",
                    request_id=request_id,
                    method=scope.get("method"),
                    route=getattr(scope.get("route"), "path", "<unmatched>"),
                    status_code=status_code,
                    duration_ms=round(
                        response_ms
                        if response_ms is not None
                        else (perf_counter() - started) * 1000,
                        2,
                    ),
                )

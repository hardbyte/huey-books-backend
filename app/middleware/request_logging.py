from time import perf_counter
from uuid import uuid4

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = structlog.get_logger()


def traffic_class(route: str, *, internal: bool = False) -> str:
    if route == "/v1/version" or (internal and route == "/"):
        return "health"
    if route == "/v1/chat/telemetry":
        return "telemetry"
    if route == "<unmatched>":
        return "other"
    if internal:
        return "webhook" if route == "/v1/process-stripe-event" else "background"
    if route.startswith("/v1/chat/") and not route.startswith("/v1/chat/admin/"):
        return "chat"
    if "webhook" in route:
        return "webhook"
    if route.startswith("/v1/"):
        return "admin"
    return "other"


class RequestLoggingMiddleware:
    def __init__(self, app: ASGIApp, *, internal: bool = False):
        self.app = app
        self.internal = internal

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        started = perf_counter()
        status_code = 500
        response_ms = None
        failed = False

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
            except Exception as exc:
                failed = True
                logger.exception(
                    "Unhandled request exception",
                    method=scope.get("method"),
                    route=getattr(scope.get("route"), "path", "<unmatched>"),
                )
                exc._huey_logged = True
                raise
            finally:
                route = getattr(scope.get("route"), "path", "<unmatched>")
                request_class = traffic_class(route, internal=self.internal)
                log = (
                    logger.error
                    if failed or status_code >= 500
                    else logger.debug
                    if request_class == "health"
                    else logger.info
                )
                log(
                    "HTTP request completed",
                    request_id=request_id,
                    method=scope.get("method"),
                    route=route,
                    traffic_class=request_class,
                    status_code=status_code,
                    response_complete=response_ms is not None,
                    failed=failed or status_code >= 500,
                    duration_ms=round(
                        response_ms
                        if response_ms is not None
                        else (perf_counter() - started) * 1000,
                        2,
                    ),
                )

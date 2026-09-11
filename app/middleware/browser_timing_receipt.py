from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.services.browser_timing import create_timing_receipt


class BrowserTimingReceiptMiddleware:
    def __init__(self, app: ASGIApp, *, secret_key: str):
        self.app = app
        self.secret_key = secret_key

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def receipt_send(message: Message) -> None:
            if (
                message["type"] == "http.response.start"
                and 200 <= message["status"] < 300
            ):
                operation = {
                    "/v1/chat/start": "start",
                    "/v1/chat/session/interact": "interact",
                    "/v1/chat/sessions/{session_token}/interact": "interact",
                }.get(getattr(scope.get("route"), "path", ""))
                request_id = scope.get("state", {}).get("request_id")
                if operation and request_id:
                    receipt = create_timing_receipt(
                        request_id, operation, self.secret_key
                    )
                    message = dict(message)
                    message["headers"] = list(message.get("headers", [])) + [
                        (b"x-response-timing-token", receipt.encode("ascii"))
                    ]
            await send(message)

        await self.app(scope, receive, receipt_send)

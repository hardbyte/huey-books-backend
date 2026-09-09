from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import HTTPException, Request, Response
from fastapi.routing import APIRoute

from app.services.workspace_errors import (
    WorkspaceConflict,
    WorkspaceError,
    WorkspaceForbidden,
    WorkspaceInvalid,
    WorkspaceNotFound,
)


class WorkspaceRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                return await handler(request)
            except WorkspaceError as exc:
                status = {
                    WorkspaceNotFound: 404,
                    WorkspaceForbidden: 403,
                    WorkspaceConflict: 409,
                    WorkspaceInvalid: 422,
                }.get(type(exc))
                if status is None:
                    raise
                raise HTTPException(status, detail=exc.detail) from exc

        return handle

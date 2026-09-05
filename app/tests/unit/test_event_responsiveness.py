import asyncio
import threading
from types import SimpleNamespace

import httpx
from fastapi import FastAPI

from app.api import events


async def test_slow_event_query_does_not_block_other_requests(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def slow_query(*args, **kwargs):
        started.set()
        release.wait(timeout=2)
        return []

    monkeypatch.setattr(
        events.event_repository, "get_all_with_optional_filters", slow_query
    )
    app = FastAPI()
    app.include_router(events.router)
    app.dependency_overrides[events.get_current_active_user_or_service_account] = (
        lambda: SimpleNamespace(id="test")
    )
    app.dependency_overrides[events.get_active_principals] = lambda: ["role:admin"]
    app.dependency_overrides[events.get_session] = lambda: None

    @app.get("/health")
    async def health():
        return {"ok": True}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        pending = asyncio.create_task(client.get("/events"))
        try:
            await asyncio.to_thread(started.wait, 1)
            response = await client.get("/health")
            assert response.status_code == 200
            assert not pending.done(), (
                "The health request must finish while the event query is still running"
            )
        finally:
            release.set()
            response = await pending
        assert response.status_code == 200

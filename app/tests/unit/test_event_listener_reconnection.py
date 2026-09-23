import asyncio
import json
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from app.services.event_listener import FlowEventListener


class FakeConnection:
    def __init__(self, *, fail_ping: bool = False) -> None:
        self.fail_ping = fail_ping
        self.closed = False
        self.listeners: list[Callable[..., Any]] = []
        self.connected = asyncio.Event()

    async def add_listener(self, channel: str, callback: Callable[..., Any]) -> None:
        assert channel == "flow_events"
        if callback not in self.listeners:
            self.listeners.append(callback)
        self.connected.set()

    async def remove_listener(self, channel: str, callback: Callable[..., Any]) -> None:
        if self.closed:
            raise ConnectionError("connection is closed")
        self.listeners.remove(callback)

    async def execute(self, command: str) -> None:
        assert command == "SELECT 1"
        await asyncio.sleep(0)
        if self.fail_ping:
            self.closed = True
            raise ConnectionError("connection is closed")

    async def close(self) -> None:
        self.closed = True

    def terminate(self) -> None:
        self.closed = True

    def is_closed(self) -> bool:
        return self.closed

    async def notify(self, payload: str) -> None:
        for callback in self.listeners:
            await callback(self, 0, "flow_events", payload)


async def test_listener_reconnects_after_closed_connection(monkeypatch: Any) -> None:
    listener = FlowEventListener()
    failed = FakeConnection(fail_ping=True)
    recovered = FakeConnection()
    connection_attempts = 0
    received = []
    listener.register_handler("node_changed", received.append)

    async def connect() -> None:
        nonlocal connection_attempts
        connection_attempts += 1
        if connection_attempts == 1:
            listener.connection = failed  # type: ignore[assignment]
        elif connection_attempts == 2:
            raise ConnectionError("database unavailable")
        else:
            listener.connection = recovered  # type: ignore[assignment]

    original_sleep = asyncio.sleep

    async def quick_sleep(seconds: float) -> None:
        await original_sleep(0)

    monkeypatch.setattr(listener, "connect", connect)
    monkeypatch.setattr(asyncio, "sleep", quick_sleep)
    try:
        await listener.start_listening()
        await asyncio.wait_for(recovered.connected.wait(), timeout=1)
        assert listener.is_listening
        assert failed.is_closed()
        assert recovered.listeners == [listener._handle_notification]
        assert connection_attempts == 3
        await recovered.notify(
            json.dumps(
                {
                    "event_type": "node_changed",
                    "session_id": str(uuid4()),
                    "flow_id": str(uuid4()),
                    "timestamp": 1.0,
                }
            )
        )
        assert len(received) == 1
    finally:
        await listener.stop_listening()


async def test_stop_cleans_up_after_connection_closes(monkeypatch: Any) -> None:
    listener = FlowEventListener()
    connection = FakeConnection()

    async def connect() -> None:
        listener.connection = connection  # type: ignore[assignment]

    monkeypatch.setattr(listener, "connect", connect)
    await listener.start_listening()
    connection.closed = True

    await listener.stop_listening()

    assert not listener.is_listening
    assert listener._listen_task is None


async def test_repeated_start_does_not_replace_active_listener(
    monkeypatch: Any,
) -> None:
    listener = FlowEventListener()
    failed = FakeConnection()
    recovered = FakeConnection()
    attempts = 0

    async def connect() -> None:
        nonlocal attempts
        attempts += 1
        listener.connection = [failed, recovered][attempts - 1]  # type: ignore[assignment]

    original_sleep = asyncio.sleep

    async def quick_sleep(seconds: float) -> None:
        await original_sleep(0)

    monkeypatch.setattr(listener, "connect", connect)
    monkeypatch.setattr(asyncio, "sleep", quick_sleep)
    try:
        await listener.start_listening()
        failed.closed = True
        await listener.start_listening()
        await asyncio.wait_for(recovered.connected.wait(), timeout=1)
        assert attempts == 2
        assert recovered.listeners == [listener._handle_notification]
    finally:
        await listener.stop_listening()


async def test_initial_connection_failure_retries(monkeypatch: Any) -> None:
    listener = FlowEventListener()
    recovered = FakeConnection()
    attempts = 0

    async def connect() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionError("database unavailable")
        listener.connection = recovered  # type: ignore[assignment]

    monkeypatch.setattr(listener, "connect", connect)
    try:
        await listener.start_listening()
        await asyncio.wait_for(recovered.connected.wait(), timeout=2)
        assert listener.is_listening
        assert recovered.listeners == [listener._handle_notification]
    finally:
        await listener.stop_listening()


async def test_stalled_ping_reconnects(monkeypatch: Any) -> None:
    from app.services import event_listener

    listener = FlowEventListener()
    stalled = FakeConnection()
    recovered = FakeConnection()
    attempts = 0

    async def stalled_execute(command: str) -> None:
        await asyncio.Event().wait()

    async def connect() -> None:
        nonlocal attempts
        attempts += 1
        listener.connection = [stalled, recovered][attempts - 1]  # type: ignore[assignment]

    original_sleep = asyncio.sleep

    async def quick_sleep(seconds: float) -> None:
        await original_sleep(0 if seconds == 30 else seconds)

    stalled.execute = stalled_execute  # type: ignore[method-assign]
    monkeypatch.setattr(listener, "connect", connect)
    monkeypatch.setattr(asyncio, "sleep", quick_sleep)
    monkeypatch.setattr(event_listener, "LISTENER_OPERATION_TIMEOUT_SECONDS", 0.01)
    try:
        await listener.start_listening()
        await asyncio.wait_for(recovered.connected.wait(), timeout=1)
        assert stalled.is_closed()
        assert recovered.listeners == [listener._handle_notification]
    finally:
        await listener.stop_listening()


async def test_concurrent_start_waits_for_stop_cleanup(monkeypatch: Any) -> None:
    listener = FlowEventListener()
    connection = FakeConnection()
    cancelling = asyncio.Event()
    release_cleanup = asyncio.Event()

    async def connect() -> None:
        listener.connection = connection  # type: ignore[assignment]

    async def worker() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelling.set()
            await release_cleanup.wait()

    monkeypatch.setattr(listener, "connect", connect)
    monkeypatch.setattr(listener, "_keep_alive", worker)
    await listener.start_listening()
    await asyncio.sleep(0)
    stop_task = asyncio.create_task(listener.stop_listening())
    await asyncio.wait_for(cancelling.wait(), timeout=1)
    start_task = asyncio.create_task(listener.start_listening())
    await asyncio.sleep(0)
    release_cleanup.set()
    await asyncio.wait_for(asyncio.gather(stop_task, start_task), timeout=1)
    try:
        assert listener.is_listening
        assert connection.listeners == [listener._handle_notification]
    finally:
        await listener.stop_listening()


async def test_stalled_unlisten_does_not_block_shutdown(monkeypatch: Any) -> None:
    from app.services import event_listener

    listener = FlowEventListener()
    connection = FakeConnection()

    async def connect() -> None:
        listener.connection = connection  # type: ignore[assignment]

    async def stalled_remove(channel: str, callback: Callable[..., Any]) -> None:
        await asyncio.Event().wait()

    connection.remove_listener = stalled_remove  # type: ignore[method-assign]
    monkeypatch.setattr(listener, "connect", connect)
    monkeypatch.setattr(event_listener, "LISTENER_OPERATION_TIMEOUT_SECONDS", 0.01)
    await listener.start_listening()
    await asyncio.wait_for(listener.stop_listening(), timeout=1)

    assert connection.is_closed()
    assert listener.connection is None

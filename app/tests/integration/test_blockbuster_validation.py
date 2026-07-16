"""Proves the BLOCKBUSTER autouse fixture actually detects event-loop blocking.

These tests only mean anything with BLOCKBUSTER=1 (which activates the autouse
`detect_blocking_calls` fixture); they are skipped otherwise so the default
suite is unaffected. They deliberately make a Python-level blocking call
(`time.sleep`) on the running loop and assert blockbuster raises.

Note: blockbuster instruments Python-level blocking (sleep, file I/O, the
stdlib socket used by sync HTTP SDKs like SendGrid/Stripe). It cannot see
blocking I/O inside C extensions such as psycopg2, so sync DB calls are not
detected by this tool.
"""

import os
import time

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from blockbuster import BlockingError

blockbuster_only = pytest.mark.skipif(
    not os.environ.get("BLOCKBUSTER"),
    reason="requires BLOCKBUSTER=1 to activate the detect_blocking_calls fixture",
)


@blockbuster_only
@pytest.mark.asyncio
async def test_detects_blocking_in_async_test():
    """A blocking call directly on the test's running loop is caught."""
    with pytest.raises(BlockingError):
        time.sleep(0.01)


_blocking_app = FastAPI()


@_blocking_app.get("/blocking")
async def _blocking_endpoint():
    time.sleep(0.01)  # blocking call on the request loop
    return {"ok": True}


@blockbuster_only
def test_detects_blocking_in_async_endpoint_via_testclient():
    """A blocking call inside an async endpoint driven by TestClient is caught."""
    client = TestClient(_blocking_app)
    with pytest.raises(BlockingError):
        client.get("/blocking")

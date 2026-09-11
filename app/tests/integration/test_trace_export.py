import subprocess
import sys

from starlette.testclient import TestClient

from app.main import app


def test_cors_preflight_has_request_id():
    response = TestClient(app).options(
        "/v1/version",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert response.headers.get("x-request-id")


def test_instrumented_pool_checkout_does_not_wait_for_export():
    result = subprocess.run(
        [sys.executable, "-m", "app.tests.util.trace_export_probe"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "70 exported spans" in result.stdout


def test_queries_are_traced_for_sync_and_async_engines_across_event_loops():
    result = subprocess.run(
        [sys.executable, "-m", "app.tests.util.sql_engine_trace_probe"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "privacy on 6 engines" in result.stdout

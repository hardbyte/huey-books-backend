"""Unit tests for enqueue_debounced_mv_refresh.

Guards the Cloud Tasks target URL: the internal API router is mounted under
API_V1_STR (/v1), so the refresh endpoint lives at
/v1/maintenance/refresh-recommendations. A task posted to
/maintenance/refresh-recommendations (no /v1) 404s and the debounced on-write
refresh silently never runs.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services import recommendations


@pytest.mark.asyncio
async def test_enqueue_targets_v1_refresh_endpoint(monkeypatch):
    captured = {}

    class FakeClient:
        def queue_path(self, project, location, queue):
            return f"projects/{project}/locations/{location}/queues/{queue}"

        def create_task(self, request):
            captured["task"] = request["task"]

    fake_settings = SimpleNamespace(
        GCP_CLOUD_TASKS_NAME="background-tasks",
        WRIVETED_INTERNAL_API="https://internal.example.run.app",
        GCP_PROJECT_ID="wriveted-api",
        GCP_LOCATION="australia-southeast1",
        GCP_CLOUD_TASKS_SERVICE_ACCOUNT="background-tasks@wriveted-api.iam.gserviceaccount.com",
    )
    monkeypatch.setattr(
        "app.config.get_settings", lambda: fake_settings, raising=True
    )

    from google.cloud import tasks_v2

    monkeypatch.setattr(tasks_v2, "CloudTasksClient", lambda: FakeClient())

    await recommendations.enqueue_debounced_mv_refresh()

    url = captured["task"]["http_request"]["url"]
    assert url == (
        "https://internal.example.run.app/v1/maintenance/refresh-recommendations"
    )
    assert url.endswith("/v1/maintenance/refresh-recommendations")


@pytest.mark.asyncio
async def test_enqueue_is_noop_when_cloud_tasks_unconfigured(monkeypatch):
    """No Cloud Tasks queue configured (local/dev/test) → no client constructed."""
    fake_settings = SimpleNamespace(
        GCP_CLOUD_TASKS_NAME=None,
        WRIVETED_INTERNAL_API="https://internal.example.run.app",
    )
    monkeypatch.setattr(
        "app.config.get_settings", lambda: fake_settings, raising=True
    )

    from google.cloud import tasks_v2

    boom = MagicMock(side_effect=AssertionError("client must not be constructed"))
    monkeypatch.setattr(tasks_v2, "CloudTasksClient", boom)

    # Should return cleanly without touching Cloud Tasks.
    await recommendations.enqueue_debounced_mv_refresh()
    boom.assert_not_called()

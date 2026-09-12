from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from sqlalchemy.dialects import postgresql

from app.api.analytics import router
from app.services.analytics import AnalyticsService


def test_only_measured_analytics_routes_are_exposed():
    app = FastAPI()
    app.include_router(router, prefix="/v1/cms")
    assert set(app.openapi()["paths"]) == {
        "/v1/cms/flows/{flow_id}/analytics",
        "/v1/cms/flows/{flow_id}/analytics/node-reach",
        "/v1/cms/flows/{flow_id}/analytics/performance",
        "/v1/cms/flows/analytics/compare",
        "/v1/cms/flows/{flow_id}/nodes/{node_id}/analytics",
        "/v1/cms/analytics/dashboard",
        "/v1/cms/analytics/real-time",
        "/v1/cms/analytics/flows/top",
    }


@pytest.mark.parametrize("total,completed,expected", [(0, 0, None), (10, 3, 0.3)])
async def test_dashboard_uses_sql_boolean_filters_and_real_completion(
    total, completed, expected
):
    results = [MagicMock() for _ in range(5)]
    results[0].scalar.return_value = 2
    results[1].scalar.return_value = 3
    results[2].scalar.return_value = 4
    results[3].first.return_value = SimpleNamespace(total=total, completed=completed)
    results[4].fetchall.return_value = []
    db = SimpleNamespace(execute=AsyncMock(side_effect=results))
    dashboard = await AnalyticsService().get_dashboard_overview(db)
    assert dashboard == {
        "overview": {
            "total_flows": 2,
            "total_content": 3,
            "active_sessions": 4,
            "completion_rate": expected,
        },
        "top_flows_by_sessions": [],
    }
    for index in (0, 1, 4):
        sql = str(
            db.execute.call_args_list[index]
            .args[0]
            .compile(dialect=postgresql.dialect())
        )
        assert "is_active IS true" in sql
        assert "WHERE false" not in sql


async def test_realtime_has_no_fabricated_latency_or_activity():
    results = [MagicMock() for _ in range(3)]
    results[0].scalar.return_value = 2
    results[1].scalar.return_value = 3
    results[2].fetchall.return_value = []
    db = SimpleNamespace(execute=AsyncMock(side_effect=results))
    snapshot = await AnalyticsService().get_real_time_metrics(db)
    assert set(snapshot) == {
        "timestamp",
        "active_sessions",
        "sessions_last_hour",
        "top_active_flows",
    }
    assert snapshot["active_sessions"] == 2
    assert snapshot["sessions_last_hour"] == 3


async def test_duration_summary_weights_measured_sessions_not_all_sessions():
    result = MagicMock()
    result.fetchall.return_value = [
        SimpleNamespace(
            period=datetime(2026, 9, 1),
            sessions=10,
            completed_sessions=1,
            avg_duration_seconds=10.0,
            duration_observations=1,
        ),
        SimpleNamespace(
            period=datetime(2026, 9, 2),
            sessions=1,
            completed_sessions=1,
            avg_duration_seconds=100.0,
            duration_observations=1,
        ),
    ]
    db = SimpleNamespace(execute=AsyncMock(return_value=result))
    report = await AnalyticsService().get_flow_performance_over_time(db, "flow")
    assert report["summary"]["avg_duration"] == 55
    assert report["summary"]["duration_observations"] == 2
    assert report["summary"]["total_sessions"] == 11


async def test_empty_performance_is_unavailable_not_fast_or_stable():
    result = MagicMock()
    result.fetchall.return_value = []
    db = SimpleNamespace(execute=AsyncMock(return_value=result))
    report = await AnalyticsService().get_flow_performance_over_time(db, "flow")
    assert report["summary"]["avg_duration"] is None
    assert report["summary"]["avg_completion_rate"] is None
    assert report["summary"]["trend"] is None


async def test_node_reach_uses_one_session_cohort_and_no_sequential_dropoff():
    results = [MagicMock() for _ in range(3)]
    results[0].all.return_value = [
        SimpleNamespace(node_id="branch", node_type="message")
    ]
    results[1].scalar.return_value = 10
    results[2].all.return_value = [SimpleNamespace(node_id="branch", sessions=3)]
    db = SimpleNamespace(execute=AsyncMock(side_effect=results))
    report = await AnalyticsService().get_flow_node_reach(
        db, "flow", date(2026, 9, 1), date(2026, 9, 2)
    )
    assert report["nodes"][0]["reached_fraction"] == 0.3
    assert "drop_off_points" not in report
    assert db.execute.await_count == 3
    for index in (1, 2):
        sql = str(
            db.execute.call_args_list[index]
            .args[0]
            .compile(dialect=postgresql.dialect())
        )
        assert "started_at >=" in sql
        assert "started_at <=" in sql


async def test_reversed_node_reach_dates_rejected_without_query():
    db = SimpleNamespace(execute=AsyncMock())
    with pytest.raises(ValueError):
        await AnalyticsService().get_flow_node_reach(
            db, "flow", date(2026, 9, 2), date(2026, 9, 1)
        )
    db.execute.assert_not_awaited()

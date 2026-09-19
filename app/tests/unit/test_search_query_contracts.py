from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from opentelemetry import trace
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags
from sqlalchemy.dialects import postgresql

from app.api.common.pagination import PaginatedQueryParams
from app.observability.tracing import RequestSampler
from app.services.recommendations import get_recommended_editions_from_mv


@pytest.mark.parametrize(
    "path",
    [
        "/v1/search",
        "/v1/recommend",
        "/v1/editions",
        "/v1/libraries",
        "/v1/lists",
        "/v1/libraries/example/collections",
        "/v1/collection/example/info",
        "/v1/public-list/example",
    ],
)
def test_read_sampling_records_under_unsampled_parent(path):
    parent = trace.set_span_in_context(
        NonRecordingSpan(
            SpanContext(
                trace_id=42, span_id=7, is_remote=True, trace_flags=TraceFlags(0)
            )
        )
    )
    sampler = RequestSampler(0, read_rate=1)
    decision = sampler.should_sample(
        parent, 42, "GET", trace.SpanKind.SERVER, {"http.target": path}
    )
    assert decision.decision.is_sampled()
    assert (
        not RequestSampler(0, read_rate=0)
        .should_sample(parent, 42, "GET", trace.SpanKind.SERVER, {"http.target": path})
        .decision.is_sampled()
    )


@pytest.mark.parametrize(
    "params", [{"skip": -1}, {"limit": 0}, {"limit": -1}, {"limit": 2001}]
)
async def test_pagination_rejects_unbounded_or_invalid_requests(params):
    app = FastAPI()

    @app.get("/page")
    def page(pagination: PaginatedQueryParams = Depends()):
        return pagination.to_dict()

    async with AsyncClient(
        transport=ASGITransport(app), base_url="http://test"
    ) as client:
        assert (await client.get("/page", params=params)).status_code == 422


async def test_recommendation_status_uses_native_enum_comparison():
    session = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(all=lambda: []))
    )
    await get_recommended_editions_from_mv(session, age=8, recommendable_only=True)
    statement = session.execute.call_args.args[0]
    compiled = str(statement.compile(dialect=postgresql.asyncpg.dialect()))
    assert "CAST(recommendable_editions.recommend_status" not in compiled
    assert "::recommendstatus" in compiled


@pytest.mark.parametrize(
    "age,expected", [(0, True), (1800, True), (1801, False), (None, False)]
)
def test_freshness_budget_includes_unknown(age, expected):
    from datetime import datetime, timedelta, timezone

    from app.services.search_freshness import freshness_status

    now = datetime.now(timezone.utc)
    snapshot = now - timedelta(seconds=age) if age is not None else None
    status = freshness_status(snapshot, now, 1800)
    assert status["within_sla"] is expected
    assert status["known"] is (age is not None)


async def test_recommendation_refresh_commits_before_reporting_success():
    from app.api.internal import handle_refresh_recommendations

    session = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock())
    await handle_refresh_recommendations(session)
    session.commit.assert_awaited_once()

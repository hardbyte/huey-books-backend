"""Integration tests for the KPIs endpoints (/v1/kpis/*).

Admin-only headline metrics and weekly trends. These assert structural
invariants (totals equal the sum of their parts, trend buckets are
consecutive zero-filled ISO weeks) rather than coupling to seed-data counts,
so they stay stable as the shared test DB changes.
"""

from datetime import date, datetime, timedelta

import pytest
from starlette import status


def _monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


@pytest.mark.asyncio
async def test_overview_requires_admin(async_client):
    """Unauthenticated callers must not reach the KPI overview."""
    response = await async_client.get("/v1/kpis/overview")
    assert response.status_code in (
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    )


@pytest.mark.asyncio
async def test_overview_structure_and_consistency(
    async_client_authenticated_as_wriveted_user,
):
    response = await async_client_authenticated_as_wriveted_user.get(
        "/v1/kpis/overview"
    )
    assert response.status_code == status.HTTP_200_OK
    body = response.json()

    schools = body["schools"]
    assert schools["total"] == (
        schools["active"] + schools["pending"] + schools["inactive"]
    )
    assert all(schools[k] >= 0 for k in ("active", "pending", "inactive", "total"))

    users = body["users"]
    assert users["total"] == sum(users["by_type"].values())

    eng = body["engagement"]
    assert eng["sessions_total"] >= (
        eng["sessions_active"] + eng["sessions_completed"] + eng["sessions_abandoned"]
    )
    assert eng["books_read"] >= 0
    if eng["completion_rate"] is not None:
        assert 0.0 <= eng["completion_rate"] <= 1.0


@pytest.mark.asyncio
async def test_trends_buckets_are_consecutive_zero_filled_weeks(
    async_client_authenticated_as_wriveted_user,
):
    weeks = 6
    response = await async_client_authenticated_as_wriveted_user.get(
        f"/v1/kpis/trends?weeks={weeks}"
    )
    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["weeks"] == weeks

    points = body["points"]
    assert len(points) == weeks

    week_dates = [date.fromisoformat(p["week"]) for p in points]
    # Every bucket is a Monday and they are consecutive, ascending.
    assert all(d.weekday() == 0 for d in week_dates)
    for earlier, later in zip(week_dates, week_dates[1:]):
        assert later - earlier == timedelta(weeks=1)

    # Final bucket is the current ISO week's Monday (UTC, tolerating a local
    # vs UTC date boundary on the test host).
    assert week_dates[-1] in {
        _monday_of(date.today()),
        _monday_of(datetime.utcnow().date()),
    }

    # Counts are present and non-negative.
    for p in points:
        for metric in (
            "new_schools",
            "new_students",
            "new_educators",
            "conversation_sessions",
            "books_read",
        ):
            assert p[metric] >= 0


@pytest.mark.asyncio
async def test_trends_weeks_param_validated(
    async_client_authenticated_as_wriveted_user,
):
    """weeks must be within [1, 52]."""
    too_many = await async_client_authenticated_as_wriveted_user.get(
        "/v1/kpis/trends?weeks=999"
    )
    assert too_many.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

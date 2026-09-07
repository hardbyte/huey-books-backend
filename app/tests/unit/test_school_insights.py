from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies.async_db_dep import get_async_session
from app.api.dependencies.school import aget_school_from_wriveted_id
from app.api.dependencies.security import get_active_principals
from app.api.dependencies.view_as import create_view_as_context, enforce_view_as
from app.api.school_insights import router
from app.db.session import get_session
from app.models.school import School
from app.models.user import UserAccountType
from app.services.school_insights import summarize
from app.services.security import create_access_token


def row(sessions=10, reached=5, feedback=0, **values):
    return {
        "week": datetime(2026, 8, 3).date(),
        "sessions": sessions,
        "reached": reached,
        "feedback": feedback,
        "liked": 0,
        "disliked": 0,
        "already_read": 0,
        "liked_sessions": 0,
        "disliked_sessions": 0,
        "read_sessions": 0,
        **values,
    }


def test_empty_is_zero_not_suppressed():
    summary, trends = summarize([], datetime(2026, 8, 3), 4)
    assert summary.sessions == 0
    assert summary.recommendation_rate is None
    assert len(trends) == 4
    assert all(point["sessions"] == 0 for point in trends)


@pytest.mark.parametrize("count", [1, 2, 3, 4])
def test_small_cohort_hidden(count):
    summary, trends = summarize([row(count, 0)], datetime(2026, 8, 3), 4)
    assert all(value is None for value in summary.model_dump().values())
    assert trends == []


def test_complementary_suppression_and_no_trend_differencing():
    summary, _ = summarize([row(10, 9)], datetime(2026, 8, 3), 4)
    assert summary.sessions == 10
    assert summary.reached_recommendations is None
    assert summary.recommendation_rate is None
    _, trends = summarize([row(10), row(1, 0)], datetime(2026, 8, 3), 4)
    assert trends == []


def test_feedback_categories_hidden_together():
    summary, _ = summarize(
        [row(10, 10, 10, liked=1, liked_sessions=1, disliked=15, disliked_sessions=9)],
        datetime(2026, 8, 3),
        4,
    )
    assert summary.feedback_sessions == 10
    assert summary.liked is summary.disliked is summary.already_read is None


@pytest.fixture
def endpoint(monkeypatch):
    school = School(id=42, wriveted_identifier=uuid4(), name="Test school")
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[aget_school_from_wriveted_id] = lambda: school
    app.dependency_overrides[get_async_session] = lambda: Mock()
    service = AsyncMock(
        return_value={
            "school_id": school.wriveted_identifier,
            "school_name": school.name,
            "start_date": "2026-08-03",
            "end_date": "2026-08-31",
            "generated_at": "2026-08-31T00:00:00Z",
            "engagement": summarize([], datetime(2026, 8, 3), 4)[0],
            "collection": {
                "works": 0,
                "labelled": 0,
                "awaiting_review": 0,
                "missing_age": 0,
                "unmatched_items": 0,
            },
            "trends": [],
            "interests": [],
        }
    )
    monkeypatch.setattr("app.api.school_insights.get_school_insights", service)
    return (
        app,
        TestClient(app),
        f"/school/{school.wriveted_identifier}/insights",
        service,
    )


@pytest.mark.parametrize("principal", ["role:admin", "educator:42", "schooladmin:42"])
def test_authorized_roles(endpoint, principal):
    app, client, path, service = endpoint
    app.dependency_overrides[get_active_principals] = lambda: [principal]
    response = client.get(path)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    service.assert_awaited_once()


@pytest.mark.parametrize(
    "principal",
    [
        "educator:99",
        "schooladmin:99",
        "student:42",
        "school:42",
        "role:lms",
        "role:parent",
        "system.Everyone",
    ],
)
def test_other_roles_denied_before_aggregation(endpoint, principal):
    app, client, path, service = endpoint
    app.dependency_overrides[get_active_principals] = lambda: [principal]
    assert client.get(path).status_code == 403
    service.assert_not_awaited()


@pytest.mark.parametrize("weeks", ["4", "12", "26"])
def test_supported_periods(endpoint, weeks):
    app, client, path, service = endpoint
    app.dependency_overrides[get_active_principals] = lambda: ["role:admin"]
    response = client.get(f"{path}?weeks={weeks}")
    assert response.status_code == 200, response.text
    assert service.call_args.args[2] == int(weeks)


@pytest.mark.parametrize("weeks", ["0", "5", "999", "invalid"])
def test_invalid_periods(endpoint, weeks):
    app, client, path, service = endpoint
    app.dependency_overrides[get_active_principals] = lambda: ["role:admin"]
    assert client.get(f"{path}?weeks={weeks}").status_code == 422
    service.assert_not_awaited()


def test_view_as_uses_target_school_not_staff_authority(monkeypatch, endpoint):
    _, _, path, service = endpoint
    from app.api.dependencies.school import aget_school_from_wriveted_id

    actor = SimpleNamespace(id=uuid4(), type=UserAccountType.WRIVETED, is_active=True)
    target = SimpleNamespace(
        id=uuid4(),
        name="Teacher",
        type=UserAccountType.EDUCATOR,
        is_active=True,
        school_id=42,
        get_principals=AsyncMock(return_value=["role:educator", "educator:42"]),
    )
    users = {str(user.id): user for user in (actor, target)}
    monkeypatch.setattr("app.crud.user.get", lambda db, id: users.get(str(id)))
    app = FastAPI(dependencies=[Depends(enforce_view_as)])
    app.include_router(router, prefix="/v1")
    app.dependency_overrides[get_session] = lambda: Mock()
    app.dependency_overrides[get_async_session] = lambda: Mock()
    school = School(id=42, name="Target", wriveted_identifier=uuid4())
    app.dependency_overrides[aget_school_from_wriveted_id] = lambda: school
    headers = {
        "Authorization": f"Bearer {create_access_token(f'Wriveted:User-Account:{actor.id}')}",
        "X-View-As": create_view_as_context(actor, target)["context"],
    }
    with TestClient(app) as client:
        assert client.get(f"/v1{path}", headers=headers).status_code == 200
        school.id = 99
        assert client.get(f"/v1{path}", headers=headers).status_code == 403
        target.is_active = False
        assert client.get(f"/v1{path}", headers=headers).status_code == 403
    assert service.await_count == 1

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies.async_db_dep import get_async_session
from app.api.dependencies.school import aget_school_from_uuid
from app.api.dependencies.security import get_active_principals
from app.api.dependencies.view_as import create_view_as_context, enforce_view_as
from app.api.school_insights import router
from app.db.session import get_session
from app.models.school import School
from app.models.user import UserAccountType
from app.services import school_insights as insights_service
from app.services.school_insights import summarize
from app.services.security import create_access_token


@pytest.mark.asyncio
async def test_snapshot_reuses_catalogue_relations():
    from app.repositories.school_insights import read_snapshot

    result = Mock()
    result.mappings.return_value.one.return_value = {
        "engagement": [],
        "collection": {},
        "interests": [],
    }
    db = AsyncMock()
    db.execute.return_value = result
    await read_snapshot(db, {})
    query = str(db.execute.await_args.args[0])
    assert query.count("JOIN editions") == 1
    assert query.count("FROM labelsets") == 1


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
    summary, trends, availability = summarize([], datetime(2026, 8, 3), 4)
    assert availability.sessions == "available"
    assert availability.recommendation_rate == "no_sessions"
    assert availability.trends == "available"
    assert summary.sessions == 0
    assert summary.recommendation_rate is None
    assert len(trends) == 4
    assert all(point["sessions"] == 0 for point in trends)


def test_unverified_feedback_withholds_choices_not_submission_count():
    summary, _, availability = summarize(
        [row(10, 10, 10, unverified_feedback=1)], datetime(2026, 8, 3), 4
    )
    assert summary.feedback_sessions == 10
    assert summary.liked is None
    assert summary.disliked is None
    assert summary.already_read is None
    assert availability.feedback_choices == "unverified_history"


@pytest.mark.parametrize("count", [1, 2, 3, 4])
def test_small_cohort_hidden(count):
    summary, trends, availability = summarize([row(count, 0)], datetime(2026, 8, 3), 4)
    assert availability.sessions == "privacy_suppressed"
    assert availability.trends == "privacy_suppressed"
    assert all(value is None for value in summary.model_dump().values())
    assert trends == []


def test_complementary_suppression_and_no_trend_differencing():
    summary, _, availability = summarize([row(10, 9)], datetime(2026, 8, 3), 4)
    assert availability.reached_recommendations == "privacy_suppressed"
    assert summary.sessions == 10
    assert summary.reached_recommendations is None
    assert summary.recommendation_rate is None
    _, trends, _ = summarize([row(10), row(1, 0)], datetime(2026, 8, 3), 4)
    assert trends == []


def test_feedback_categories_hidden_together():
    summary, _, availability = summarize(
        [row(10, 10, 10, liked=1, liked_sessions=1, disliked=15, disliked_sessions=9)],
        datetime(2026, 8, 3),
        4,
    )
    assert summary.feedback_sessions == 10
    assert summary.liked is summary.disliked is summary.already_read is None
    assert availability.feedback_choices == "privacy_suppressed"


def test_privacy_reason_does_not_disclose_unverified_history_in_small_groups():
    _, _, availability = summarize(
        [row(4, 4, 4, unverified_feedback=1)], datetime(2026, 8, 3), 4
    )
    assert availability.feedback_choices == "privacy_suppressed"


@pytest.mark.asyncio
@pytest.mark.parametrize("weeks", [4, 12, 26])
@pytest.mark.parametrize(
    "now", ["2026-09-06T23:59:59+00:00", "2026-09-07T00:00:00+00:00"]
)
async def test_complete_utc_week_boundaries(monkeypatch, weeks, now):
    instant = datetime.fromisoformat(now)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == timezone.utc
            return instant

    snapshot = AsyncMock(
        return_value={
            "engagement": [],
            "collection": {
                key: 0
                for key in (
                    "works",
                    "labelled",
                    "awaiting_review",
                    "missing_age",
                    "unmatched_items",
                )
            },
            "interests": [],
        }
    )
    monkeypatch.setattr(insights_service, "datetime", Clock)
    monkeypatch.setattr(insights_service.repository, "read_snapshot", snapshot)
    school = School(school_uuid=uuid4(), name="Test school")
    report = await insights_service.get_school_insights(Mock(), school, weeks)
    expected_end = (
        datetime(2026, 8, 31) if instant.weekday() == 6 else datetime(2026, 9, 7)
    )
    assert report.end_date == expected_end.date()
    assert report.start_date == (expected_end - timedelta(weeks=weeks)).date()
    assert report.generated_at == instant
    assert len(report.trends) == weeks
    assert snapshot.call_args.args[1]["end"] == expected_end


@pytest.fixture
def endpoint(monkeypatch):
    school = School(id=42, wriveted_identifier=uuid4(), name="Test school")
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[aget_school_from_uuid] = lambda: school
    app.dependency_overrides[get_async_session] = lambda: Mock()
    service = AsyncMock(
        return_value={
            "school_uuid": school.school_uuid,
            "school_name": school.name,
            "start_date": "2026-08-03",
            "end_date": "2026-08-31",
            "generated_at": "2026-08-31T00:00:00Z",
            "engagement": summarize([], datetime(2026, 8, 3), 4)[0],
            "availability": summarize([], datetime(2026, 8, 3), 4)[2],
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
    assert response.json()["school_uuid"] == response.json()["school_id"]
    service.assert_awaited_once()


def test_identifier_contract_in_openapi(endpoint):
    app, _, _, _ = endpoint
    schema = app.openapi()
    operation = schema["paths"]["/school/{school_uuid}/insights"]["get"]
    assert any(
        parameter["name"] == "school_uuid" for parameter in operation["parameters"]
    )
    response_schema = schema["components"]["schemas"]["SchoolInsights"]
    assert response_schema["properties"]["school_id"]["deprecated"] is True
    assert "school_uuid" in response_schema["required"]
    assert schema["components"]["schemas"]["InsightsWeeks"]["enum"] == [4, 12, 26]


def test_report_semantics_are_explicit(endpoint):
    app, client, path, _ = endpoint
    app.dependency_overrides[get_active_principals] = lambda: ["role:admin"]
    response = client.get(path)
    assert response.status_code == 200, response.text
    assert response.json()["semantics"] == {
        "timezone": "UTC",
        "end_date_exclusive": True,
        "activity_basis": "session_started_at",
        "outcomes_basis": "latest_available",
        "feedback_basis": "latest_recorded_submission",
        "interests_basis": "current_session_state",
        "collection_basis": "current_snapshot",
        "feedback_unit": "isbn_choices_per_session",
    }
    assert response.json()["availability"]["interests"] == "privacy_filtered"


def test_neutral_model_alias_uses_same_identifier():
    school = School(school_uuid=uuid4())
    assert school.school_uuid == school.wriveted_identifier
    school.wriveted_identifier = uuid4()
    assert school.school_uuid == school.wriveted_identifier
    assert "school_uuid" not in School.__table__.columns
    assert "wriveted_identifier" in School.__table__.columns


def test_legacy_response_input_is_normalized(endpoint):
    app, client, path, service = endpoint
    app.dependency_overrides[get_active_principals] = lambda: ["role:admin"]
    legacy_uuid = service.return_value.pop("school_uuid")
    service.return_value["school_id"] = legacy_uuid
    response = client.get(path)
    assert response.status_code == 200, response.text
    assert response.json()["school_uuid"] == str(legacy_uuid)
    assert response.json()["school_id"] == str(legacy_uuid)


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
    from app.api.dependencies.school import aget_school_from_uuid

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
    app.dependency_overrides[aget_school_from_uuid] = lambda: school
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

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.api import schools
from app.api.common.pagination import PaginatedQueryParams
from app.models import SchoolState


@pytest.mark.asyncio
@pytest.mark.parametrize("details", [False, True])
@pytest.mark.parametrize("collection_access", [False, True])
@pytest.mark.parametrize("subscribed", [False, True])
@pytest.mark.parametrize("empty_info", [False, True])
@pytest.mark.parametrize("missing_country", [False, True])
async def test_selector_redacts_response_without_mutating_source(
    monkeypatch, details, collection_access, subscribed, empty_info, missing_country
):
    subscription = SimpleNamespace(
        id="sub_selector",
        provider="stripe",
        is_active=True,
        product=SimpleNamespace(id="price_selector", name="School"),
    )
    collection = SimpleNamespace(
        id=uuid4(), name="Library", book_count=10, updated_at=datetime.now(timezone.utc)
    )
    school = SimpleNamespace(
        wriveted_identifier=uuid4(),
        name="Selector school",
        country_code="NZL",
        state=SchoolState.ACTIVE,
        subscription=subscription,
        collection=collection,
        info={
            "location": {},
            "terms_acceptance": {"huey_books": {"accepted_by_user_id": "private"}},
            "experiments": {"private_experiment": True},
        },
        admins=[
            SimpleNamespace(
                id=uuid4(),
                name="Private administrator",
                type="school_admin",
                email="private@example.com",
                is_active=True,
                last_login_at=datetime.now(timezone.utc),
            )
        ],
    )
    if empty_info:
        school.info = None
    if missing_country:
        school.country_code = None
    get_schools = AsyncMock(return_value=[school])
    monkeypatch.setattr(
        schools.school_repository, "get_all_with_optional_filters", get_schools
    )
    monkeypatch.setattr(
        schools,
        "has_permission",
        lambda principals, permission, acl: {
            "details": details,
            "read-collection": collection_access,
        }[permission],
    )

    result = await schools.get_schools(
        session=AsyncMock(),
        country_code=None,
        state=None,
        postcode=None,
        q=None,
        is_active=True,
        has_active_subscription=subscribed,
        connected_collection=True,
        official_identifier=None,
        pagination=PaginatedQueryParams(skip=0, limit=10),
        principals=[],
    )

    assert (result[0].state is not None) is details
    assert (result[0].subscription is not None) is details
    assert (result[0].collection is not None) is collection_access
    assert school.state == SchoolState.ACTIVE
    assert school.subscription is subscription
    assert school.collection is collection
    serialized = result[0].model_dump(mode="json")
    assert serialized["country_code"] == school.country_code
    if details:
        assert serialized["admins"][0]["email"] == "private@example.com"
        assert serialized["info"]["terms_acceptance"] == (school.info or {}).get(
            "terms_acceptance"
        )
        assert serialized["info"]["experiments"] == (school.info or {}).get(
            "experiments"
        )
    else:
        assert serialized["admins"] == [{}]
        assert serialized["info"]["terms_acceptance"] is None
        assert serialized["info"]["experiments"] is None
    assert school.admins[0].email == "private@example.com"
    if empty_info:
        assert school.info is None
    else:
        assert school.info["terms_acceptance"] is not None
        assert school.info["experiments"] is not None
    filters = get_schools.await_args.kwargs
    assert filters["has_active_subscription"] is (subscribed if details else None)
    assert filters["is_active"] is (True if details else None)
    assert filters["is_collection_connected"] is (True if details else None)

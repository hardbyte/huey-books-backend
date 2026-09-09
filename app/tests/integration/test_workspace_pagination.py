from uuid import uuid4

import pytest
from sqlalchemy import delete

from app.models.educator import Educator
from app.models.organisation import (
    LibraryMembership,
    Organisation,
    OrganisationMembership,
)
from app.models.user import User


@pytest.fixture
def paged_workspace(session, test_school, test_schooladmin_account):
    organisations = [
        Organisation(name="Pagination organisation", kind="school") for _ in range(101)
    ]
    session.add_all(organisations)
    session.flush()
    organisation_ids = [organisation.id for organisation in organisations]
    people = [
        Educator(
            name="Pagination member",
            email=f"pagination-{uuid4()}@example.org",
            school_id=test_school.id,
            is_active=True,
        )
        for _ in range(101)
    ]
    session.add_all(people)
    session.flush()
    people_ids = [person.id for person in people]
    session.add_all(
        [
            OrganisationMembership(
                organisation_id=organisation_id, user_id=test_schooladmin_account.id
            )
            for organisation_id in organisation_ids
        ]
    )
    session.add_all(
        [
            OrganisationMembership(
                organisation_id=organisation_ids[0], user_id=person_id
            )
            for person_id in people_ids
        ]
    )
    session.add_all(
        [
            LibraryMembership(
                school_id=test_school.id, user_id=person_id, role="reviewer"
            )
            for person_id in people_ids
        ]
    )
    session.commit()
    yield organisation_ids, people_ids, str(test_school.school_uuid)
    session.rollback()
    session.execute(delete(Organisation).where(Organisation.id.in_(organisation_ids)))
    session.execute(delete(User).where(User.id.in_(people_ids)))
    session.commit()


async def test_organisation_pages_reach_beyond_first_hundred(
    async_client, paged_workspace, test_schooladmin_account_headers
):
    organisation_ids, _, _ = paged_workspace
    expected = sorted(str(identifier) for identifier in organisation_ids)
    found = []
    for skip in (0, 100):
        response = await async_client.get(
            "/v1/organisations",
            params={"skip": skip},
            headers=test_schooladmin_account_headers,
        )
        assert response.status_code == 200, response.text
        page = response.json()
        assert (page["total"], page["skip"], page["limit"]) == (101, skip, 100)
        found.extend(item["id"] for item in page["data"])
    assert found == expected


@pytest.mark.parametrize("kind", ["organisation", "library"])
async def test_member_pages_are_complete_and_stable(
    async_client,
    paged_workspace,
    test_wrivetedadmin_account_headers,
    test_schooladmin_account,
    kind,
):
    organisation_ids, people_ids, library_uuid = paged_workspace
    path = (
        f"/v1/organisations/{organisation_ids[0]}/members"
        if kind == "organisation"
        else f"/v1/libraries/{library_uuid}/members"
    )
    expected_ids = {str(identifier) for identifier in people_ids}
    if kind == "organisation":
        expected_ids.add(str(test_schooladmin_account.id))
    pages = []
    for skip in (0, 100):
        response = await async_client.get(
            path, params={"skip": skip}, headers=test_wrivetedadmin_account_headers
        )
        assert response.status_code == 200, response.text
        page = response.json()
        assert (page["total"], page["skip"], page["limit"]) == (
            len(expected_ids),
            skip,
            100,
        )
        assert len(page["data"]) <= 100
        pages.extend(page["data"])
    assert {item["user_id"] for item in pages} == expected_ids
    assert len(pages) == len(expected_ids)
    tied = [item["user_id"] for item in pages if item["name"] == "Pagination member"]
    assert tied == sorted(tied)


@pytest.mark.parametrize("query", [{"limit": 101}, {"limit": 0}, {"skip": -1}])
@pytest.mark.parametrize(
    "path",
    [
        "/v1/organisations",
        "/v1/organisations/{id}/members",
        "/v1/libraries/{id}/members",
    ],
)
async def test_workspace_pagination_rejects_unbounded_requests(
    async_client, test_wrivetedadmin_account_headers, query, path
):
    response = await async_client.get(
        path.format(id=uuid4()),
        params=query,
        headers=test_wrivetedadmin_account_headers,
    )
    assert response.status_code == 422, response.text

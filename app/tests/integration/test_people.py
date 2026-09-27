from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.pool import NullPool

from app.models import Educator, School, User
from app.models.event_outbox import EventOutbox
from app.models.organisation import (
    LibraryMembership,
    Organisation,
    OrganisationMembership,
)
from app.services.security import create_access_token


@pytest.fixture
def people_connections(session):
    engine = create_engine(session.get_bind().url, poolclass=NullPool)
    yield engine
    engine.dispose()


def test_concurrent_admin_departures_keep_one_admin(
    session, people_setup, people_connections
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from sqlalchemy.orm import Session

    from app.models.school_admin import SchoolAdmin
    from app.schemas.users.user_update import UserUpdateIn
    from app.services.account_access import validate_account_access_change
    from app.services.workspace_errors import WorkspaceConflict

    prefix, school, _ = people_setup
    admins = [
        SchoolAdmin(
            name=f"{prefix}-{index}",
            email=f"{prefix}-{index}@example.com",
            school_id=school.id,
            is_active=True,
        )
        for index in range(2)
    ]
    session.add_all(admins)
    session.commit()
    user_ids = [admin.id for admin in admins]
    barrier = Barrier(2)

    def deactivate(user_id):
        with Session(people_connections) as connection:
            user = connection.get(SchoolAdmin, user_id)
            barrier.wait(timeout=5)
            try:
                validate_account_access_change(
                    connection, user, UserUpdateIn(is_active=False), is_staff=True
                )
                user.is_active = False
                connection.commit()
                return "changed"
            except WorkspaceConflict:
                connection.rollback()
                return "protected"

    with ThreadPoolExecutor(max_workers=2) as workers:
        assert sorted(workers.map(deactivate, user_ids)) == ["changed", "protected"]


def test_stale_home_assignment_is_rejected(
    session, people_setup, test_school, people_connections
):
    from sqlalchemy import update
    from sqlalchemy.orm import Session

    from app.models.school_admin import SchoolAdmin
    from app.schemas.users.user_update import UserUpdateIn
    from app.services.account_access import validate_account_access_change
    from app.services.workspace_errors import WorkspaceConflict

    prefix, school, _ = people_setup
    user = SchoolAdmin(
        name=prefix, email=f"{prefix}@example.com", school_id=school.id, is_active=True
    )
    session.add(user)
    session.commit()
    with Session(people_connections) as stale:
        account = stale.get(SchoolAdmin, user.id)
        with Session(people_connections) as moving:
            moving.execute(
                update(Educator.__table__)
                .where(Educator.__table__.c.id == user.id)
                .values(school_id=test_school.id)
            )
            moving.commit()
        with pytest.raises(WorkspaceConflict, match="membership changed"):
            validate_account_access_change(
                stale, account, UserUpdateIn(is_active=False), is_staff=True
            )


def headers(user_id):
    return {
        "Authorization": "Bearer "
        + create_access_token(
            subject=f"wriveted:user-account:{user_id}",
            expires_delta=timedelta(minutes=5),
        )
    }


@pytest.fixture
def people_setup(session, test_school, monkeypatch):
    async def no_dispatch():
        pass

    monkeypatch.setattr("app.api.people.trigger_email_delivery_async", no_dispatch)
    prefix = f"people-{uuid4()}"
    organisation = Organisation(name=prefix, kind="school")
    session.add(organisation)
    session.flush()
    other = School(
        name=prefix,
        country_code=test_school.country_code,
        organisation_id=organisation.id,
        info={"location": {}},
    )
    session.add(other)
    session.commit()
    yield prefix, other, organisation
    session.rollback()
    ids = select(User.id).where(User.name.startswith(prefix))
    session.execute(delete(EventOutbox).where(EventOutbox.user_id.in_(ids)))
    session.execute(delete(User).where(User.name.startswith(prefix)))
    session.execute(delete(School).where(School.id == other.id))
    session.execute(delete(Organisation).where(Organisation.id == organisation.id))
    session.commit()


async def test_case_insensitive_invited_identity(
    async_client, session, people_setup, test_school, test_schooladmin_account_headers
):
    from app import crud
    from app.schemas.users.user_create import UserCreateIn

    prefix, _, _ = people_setup
    email = f"{prefix}@example.com"
    path = f"/v1/libraries/{test_school.school_uuid}/people"
    result = await async_client.post(
        path,
        headers=test_schooladmin_account_headers,
        json={"name": prefix, "email": email, "role": "reviewer"},
    )
    assert result.status_code == 204, result.text
    invited = crud.user.get_by_account_email(session, email.upper())
    signed_in, created = crud.user.get_or_create(
        session, UserCreateIn(name=prefix, email=email.upper())
    )
    assert signed_in.id == invited.id and not created
    session.rollback()


async def test_school_people_add_change_remove_preserves_account_and_other_library(
    async_client,
    session,
    people_setup,
    test_school,
    test_schooladmin_account_headers,
    test_wrivetedadmin_account_headers,
):
    prefix, other, _ = people_setup
    path = f"/v1/libraries/{test_school.school_uuid}/people"
    admin = test_schooladmin_account_headers
    response = await async_client.post(
        path,
        headers=admin,
        json={"name": prefix, "email": f"{prefix}@example.com", "role": "educator"},
    )
    assert response.status_code == 204, response.text
    response = await async_client.get(path, headers=admin, params={"q": prefix})
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    person = response.json()["data"][0]
    user_id = UUID(person["user_id"])
    assert person["access"] == [
        {
            "source": "home",
            "role": "educator",
            "can_edit": True,
            "can_remove": True,
            "manage_url": None,
        }
    ]
    assert person["last_login_at"] is None
    assert session.scalar(select(EventOutbox.id).where(EventOutbox.user_id == user_id))
    other_path = f"/v1/libraries/{other.school_uuid}/people"
    added = await async_client.post(
        other_path,
        headers=test_wrivetedadmin_account_headers,
        json={"name": prefix, "email": f"{prefix}@example.com", "role": "reviewer"},
    )
    assert added.status_code == 204, added.text
    promoted = await async_client.patch(
        f"{path}/{user_id}/home",
        headers=admin,
        json={"role": "school_admin", "expected_role": "educator"},
    )
    assert promoted.status_code == 204, promoted.text
    stale = await async_client.delete(
        f"{path}/{user_id}/home?expected_role=educator", headers=admin
    )
    assert stale.status_code == 409, stale.text
    removed = await async_client.delete(
        f"{path}/{user_id}/home?expected_role=school_admin", headers=admin
    )
    assert removed.status_code == 204, removed.text
    assert (
        session.scalar(select(Educator.school_id).where(Educator.id == user_id)) is None
    )
    assert session.scalar(select(User.is_active).where(User.id == user_id)) is True
    assert session.get(LibraryMembership, (other.id, user_id))
    me = await async_client.get("/v1/auth/me", headers=headers(user_id))
    assert me.status_code == 200, me.text
    assert me.json()["user"]["school"] is None
    assert (await async_client.get(path, headers=headers(user_id))).status_code in (
        403,
        404,
    )
    assert (
        await async_client.get(
            f"/v1/libraries/{other.school_uuid}", headers=headers(user_id)
        )
    ).status_code == 200
    from app import crud
    from app.services.review_access import can_review

    account = crud.user.get(session, id=user_id)
    assert can_review(session, account)
    downgrade = await async_client.patch(
        f"{other_path}/{user_id}/direct",
        headers=test_wrivetedadmin_account_headers,
        json={"role": "cataloguer", "expected_role": "reviewer"},
    )
    assert downgrade.status_code == 204, downgrade.text
    assert not can_review(session, account)
    assert (
        await async_client.post("/v1/work/1/reviews", headers=headers(user_id), json={})
    ).status_code == 403
    removed = await async_client.delete(
        f"{other_path}/{user_id}/direct?expected_role=cataloguer",
        headers=test_wrivetedadmin_account_headers,
    )
    assert removed.status_code == 204, removed.text
    assert not can_review(session, account)
    assert (
        await async_client.get(
            f"/v1/libraries/{other.school_uuid}", headers=headers(user_id)
        )
    ).status_code == 404


async def test_last_school_admin_and_library_manager_cannot_escalate(
    async_client,
    people_setup,
    test_school,
    test_schooladmin_account,
    test_schooladmin_account_headers,
    test_wrivetedadmin_account_headers,
):
    prefix, other, _ = people_setup
    path = f"/v1/libraries/{test_school.school_uuid}/people"
    response = await async_client.delete(
        f"{path}/{test_schooladmin_account.id}/home?expected_role=school_admin",
        headers=test_schooladmin_account_headers,
    )
    assert response.status_code == 409, response.text
    bypass = await async_client.patch(
        f"/v1/user/{test_schooladmin_account.id}",
        headers=test_schooladmin_account_headers,
        json={"type": "educator"},
    )
    assert bypass.status_code == 403, bypass.text
    for change in (
        {"type": "educator"},
        {"is_active": False},
        {"school_id": str(other.school_uuid)},
    ):
        bypass = await async_client.patch(
            f"/v1/user/{test_schooladmin_account.id}",
            headers=test_wrivetedadmin_account_headers,
            json=change,
        )
        assert bypass.status_code == 409, bypass.text
    bypass = await async_client.delete(
        f"/v1/user/{test_schooladmin_account.id}",
        headers=test_wrivetedadmin_account_headers,
    )
    assert bypass.status_code == 409, bypass.text
    other_path = f"/v1/libraries/{other.school_uuid}/people"
    response = await async_client.post(
        other_path,
        headers=test_wrivetedadmin_account_headers,
        json={
            "name": prefix,
            "email": test_schooladmin_account.email,
            "role": "manager",
        },
    )
    assert response.status_code == 204, response.text
    listing = await async_client.get(
        other_path, headers=test_schooladmin_account_headers
    )
    assert listing.status_code == 200, listing.text
    assert "school_admin" not in listing.json()["available_roles"]
    denied = await async_client.post(
        other_path,
        headers=test_schooladmin_account_headers,
        json={"name": prefix, "email": f"{prefix}@example.com", "role": "school_admin"},
    )
    assert denied.status_code == 403, denied.text
    wrong_scope = await async_client.delete(
        f"{other_path}/{test_schooladmin_account.id}/home?expected_role=school_admin",
        headers=test_schooladmin_account_headers,
    )
    assert wrong_scope.status_code == 404, wrong_scope.text


async def test_staff_can_reactivate_library_only_person(
    async_client, session, people_setup, test_wrivetedadmin_account_headers
):
    prefix, _, _ = people_setup
    person = Educator(
        name=prefix, email=f"{prefix}@example.com", school_id=None, is_active=False
    )
    session.add(person)
    session.commit()
    response = await async_client.patch(
        f"/v1/user/{person.id}",
        headers=test_wrivetedadmin_account_headers,
        json={"is_active": True},
    )
    assert response.status_code == 200, response.text
    assert response.json()["is_active"] is True


async def test_people_inherited_access_and_resend_throttle(
    async_client,
    session,
    people_setup,
    test_schooladmin_account,
    test_wrivetedadmin_account_headers,
):
    _, other, organisation = people_setup
    session.add(
        OrganisationMembership(
            organisation_id=organisation.id, user_id=test_schooladmin_account.id
        )
    )
    session.commit()
    path = f"/v1/libraries/{other.school_uuid}/people"
    response = await async_client.get(path, headers=test_wrivetedadmin_account_headers)
    assert response.status_code == 200, response.text
    inherited = response.json()["data"][0]["access"][0]
    assert inherited["source"] == "organisation" and not inherited["can_remove"]
    assert inherited["manage_url"]
    assert (
        await async_client.delete(
            f"{path}/{test_schooladmin_account.id}/organisation?expected_role=manager",
            headers=test_wrivetedadmin_account_headers,
        )
    ).status_code == 404
    org_path = f"/v1/organisations/{organisation.id}/people"
    response = await async_client.post(
        f"{org_path}/{test_schooladmin_account.id}/resend",
        headers=test_wrivetedadmin_account_headers,
    )
    assert response.status_code == 204, response.text
    assert (
        await async_client.post(
            f"{org_path}/{test_schooladmin_account.id}/resend",
            headers=test_wrivetedadmin_account_headers,
        )
    ).status_code == 409
    assert (await async_client.get(path)).status_code in (401, 403)

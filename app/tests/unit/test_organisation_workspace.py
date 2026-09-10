from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.user import UserAccountType
from app.schemas.organisation import (
    CollectionCreate,
    CollectionImport,
    ImportItem,
    LibraryDetailsUpdate,
    MemberChange,
)
from app.services.organisation_workspace import library_access, manages_organisation
from app.services.workspace_errors import WorkspaceConflict, WorkspaceNotFound


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("count", [0, 1, 99, 100])
@pytest.mark.parametrize("writer", [False, True])
def test_collection_creation_capability_matches_policy(
    monkeypatch, enabled, count, writer
):
    from app.config import get_settings
    from app.services.organisation_workspace import LibraryAccess, _summary_capabilities

    monkeypatch.setattr(get_settings(), "MULTIPLE_COLLECTIONS_ENABLED", enabled)
    permissions = frozenset(
        {"catalogue_read", "catalogue_write"} if writer else {"catalogue_read"}
    )
    access = LibraryAccess(SimpleNamespace(), permissions, ())
    capabilities = _summary_capabilities(access, count)
    assert ("create_collection" in capabilities) == (
        writer and count < 100 and (count == 0 or enabled)
    )
    assert access.capabilities == permissions


def test_library_details_cannot_change_education_or_billing_scope():
    for field in ("country_code", "organisation_id", "student_domain", "state"):
        with pytest.raises(ValidationError):
            LibraryDetailsUpdate(
                name="Library", expected_name="Old", **{field: "value"}
            )
    with pytest.raises(ValidationError):
        LibraryDetailsUpdate(name="   ", expected_name="Old")


def test_missing_school_location_is_valid_optional_metadata():
    from app.schemas.school import SchoolInfo

    assert SchoolInfo.model_validate({"seed_key": "demo"}).location.lat is None


@pytest.mark.parametrize("role,allowed", [("manager", True), ("reviewer", False)])
async def test_library_detail_capability_is_explicit(role, allowed):
    actor = SimpleNamespace(
        id=uuid4(), is_active=True, type=UserAccountType.EDUCATOR, school_id=99
    )
    session = AsyncMock()
    session.get.return_value = SimpleNamespace(role=role)
    access = await library_access(
        session, actor, SimpleNamespace(id=1, organisation_id=None)
    )
    assert ("manage_details" in access.capabilities) is allowed


@pytest.mark.parametrize("current_name,status", [("Old", None), ("Changed", 409)])
async def test_library_details_optimistic_update(monkeypatch, current_name, status):
    from app.services.organisation_management import update_library_details

    library = SimpleNamespace(name=current_name)
    access = SimpleNamespace(library=library)
    resolver = AsyncMock(return_value=access)
    monkeypatch.setattr(
        "app.services.organisation_management.resolve_library", resolver
    )
    monkeypatch.setattr(
        "app.services.organisation_management.library_summary",
        AsyncMock(return_value="summary"),
    )
    session = AsyncMock()
    session.scalar.return_value = library
    actor = SimpleNamespace(id=uuid4())
    library_uuid = uuid4()
    data = LibraryDetailsUpdate(name="New", expected_name="Old")
    if status:
        with pytest.raises(WorkspaceConflict) as failure:
            await update_library_details(library_uuid, data, session, actor)
        assert isinstance(failure.value, WorkspaceConflict)
        session.commit.assert_not_called()
        assert library.name == current_name
    else:
        assert (
            await update_library_details(library_uuid, data, session, actor)
            == "summary"
        )
        assert library.name == "New"
        session.commit.assert_awaited_once()
    resolver.assert_awaited_once_with(session, actor, library_uuid, "manage_details")


def test_import_normalises_isbn_and_rejects_equivalent_duplicates():
    assert ImportItem(edition_isbn="0140328726").edition_isbn == "9780140328721"
    with pytest.raises(ValidationError, match="duplicate ISBN"):
        CollectionImport(
            items=[{"edition_isbn": "0140328726"}, {"edition_isbn": "9780140328721"}]
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"edition_isbn": "9780140328720"},
        {"edition_isbn": "9780140328721", "copies_total": -1},
        {"edition_isbn": "9780140328721", "copies_total": 1.5},
    ],
)
def test_import_rejects_invalid_rows(payload):
    with pytest.raises(ValidationError):
        ImportItem(**payload)


def test_mutation_payloads_cannot_select_ownership_or_escalate_role():
    with pytest.raises(ValidationError):
        CollectionCreate(name="Collection", is_default=True)
    with pytest.raises(ValidationError):
        CollectionCreate(name="Collection", school_id=str(uuid4()))
    with pytest.raises(ValidationError):
        MemberChange(role="platform_staff")


@pytest.mark.parametrize(
    "account_type",
    [UserAccountType.STUDENT, UserAccountType.PUBLIC, UserAccountType.PARENT],
)
async def test_changed_account_role_loses_memberships(account_type):
    actor = SimpleNamespace(id=uuid4(), is_active=True, type=account_type)
    library = SimpleNamespace(id=1, organisation_id=uuid4())
    session = AsyncMock()
    session.get.return_value = SimpleNamespace(role="manager")
    assert not (await library_access(session, actor, library)).capabilities
    assert not await manages_organisation(session, actor, library.organisation_id)


async def test_inactive_platform_staff_has_no_access():
    actor = SimpleNamespace(id=uuid4(), is_active=False, type=UserAccountType.WRIVETED)
    library = SimpleNamespace(id=1, organisation_id=uuid4())
    session = AsyncMock()
    assert not (await library_access(session, actor, library)).capabilities
    assert not await manages_organisation(session, actor, library.organisation_id)


@pytest.mark.parametrize("operation", ["list_libraries", "list_organisations"])
async def test_inactive_staff_cannot_discover_workspaces(operation):
    from app.services import organisation_management

    session = AsyncMock()
    actor = SimpleNamespace(id=uuid4(), is_active=False, type=UserAccountType.WRIVETED)
    result = await getattr(organisation_management, operation)(session, actor)
    assert (result["data"] if isinstance(result, dict) else result.data) == []
    session.scalars.assert_not_called()
    session.scalar.assert_not_called()


async def test_library_reviewer_gets_no_catalogue_write_or_membership_permission():
    actor = SimpleNamespace(
        id=uuid4(), is_active=True, type=UserAccountType.EDUCATOR, school_id=99
    )
    library = SimpleNamespace(id=1, organisation_id=None)
    session = AsyncMock()
    session.get.return_value = SimpleNamespace(role="reviewer")
    access = await library_access(session, actor, library)
    assert access.capabilities == {"catalogue_read", "review", "insights"}
    assert access.sources == ("library_reviewer",)


async def test_import_deleted_collection_fails_before_any_write(monkeypatch):
    from app.services.organisation_management import import_collection

    library_uuid = uuid4()
    actor = SimpleNamespace(id=uuid4())
    access = SimpleNamespace(library=SimpleNamespace(school_uuid=library_uuid))
    monkeypatch.setattr(
        "app.services.organisation_management.resolve_library",
        AsyncMock(return_value=access),
    )
    session = AsyncMock()
    session.scalar.return_value = None
    data = CollectionImport(items=[{"edition_isbn": "9780140328721"}], dry_run=False)
    with pytest.raises(WorkspaceNotFound) as failure:
        await import_collection(library_uuid, uuid4(), data, session, actor)
    assert isinstance(failure.value, WorkspaceNotFound)
    session.execute.assert_not_called()
    session.commit.assert_not_called()

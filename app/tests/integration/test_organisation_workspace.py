import asyncio
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.models import (
    Collection,
    CollectionItem,
    Edition,
    Educator,
    School,
    SchoolState,
    Work,
)
from app.models.organisation import (
    Organisation,
    OrganisationMembership,
    OrganisationSubscription,
)
from app.models.subscription import Subscription, SubscriptionType
from app.services.editions import generate_random_valid_isbn13


@pytest.fixture(autouse=True)
def enabled_multiple_collections(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "MULTIPLE_COLLECTIONS_ENABLED", True)


async def test_rollout_gate_blocks_additional_collections(
    async_client, workspace, test_wrivetedadmin_account_headers, monkeypatch
):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "MULTIPLE_COLLECTIONS_ENABLED", False)
    _, libraries, _ = workspace
    response = await async_client.post(
        f"/v1/libraries/{libraries[0]}/collections",
        headers=test_wrivetedadmin_account_headers,
        json={"name": "Not yet"},
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "Additional collections are not enabled yet"


async def test_cataloguer_can_import_but_cannot_administer_or_review(
    async_client,
    workspace,
    test_schooladmin_account,
    test_schooladmin_account_headers,
    test_wrivetedadmin_account_headers,
):
    organisation, libraries, collections = workspace
    staff = test_wrivetedadmin_account_headers
    actor = test_schooladmin_account_headers
    path = f"/v1/libraries/{libraries[0]}"
    granted = await async_client.put(
        f"{path}/members/{test_schooladmin_account.id}",
        headers=staff,
        json={"role": "cataloguer"},
    )
    assert granted.status_code == 200, granted.text
    detail = await async_client.get(path, headers=actor)
    assert detail.json()["capabilities"] == ["catalogue_read", "catalogue_write"]
    imported = await async_client.post(
        f"{path}/collections/{collections[0]}/import",
        headers=actor,
        json={
            "dry_run": False,
            "items": [{"edition_isbn": "9780140328721", "copies_total": 3}],
        },
    )
    assert imported.status_code == 200, imported.text
    assert imported.json()["added"] == 1
    for suffix in ("members", "review-queue", "insights"):
        response = await async_client.get(f"{path}/{suffix}", headers=actor)
        assert response.status_code == 403, response.text
    response = await async_client.patch(
        path,
        headers=actor,
        json={"name": "Forbidden", "expected_name": detail.json()["name"]},
    )
    assert response.status_code == 403, response.text
    sibling = await async_client.get(f"/v1/libraries/{libraries[1]}", headers=actor)
    assert sibling.status_code == 404, sibling.text
    invalid_org_role = await async_client.put(
        f"/v1/organisations/{organisation}/members/{test_schooladmin_account.id}",
        headers=staff,
        json={"role": "cataloguer"},
    )
    assert invalid_org_role.status_code == 422, invalid_org_role.text


@pytest.mark.parametrize("operation", ["remove", "demote"])
async def test_last_library_manager_is_retained(
    async_client,
    workspace,
    test_schooladmin_account,
    test_schooladmin_account_headers,
    test_wrivetedadmin_account_headers,
    operation,
):
    organisation, libraries, _ = workspace
    path = f"/v1/libraries/{libraries[0]}/members/{test_schooladmin_account.id}"
    staff = test_wrivetedadmin_account_headers
    manager = test_schooladmin_account_headers
    granted = await async_client.put(path, headers=staff, json={"role": "manager"})
    assert granted.status_code == 200, granted.text
    if operation == "remove":
        response = await async_client.delete(path, headers=manager)
    else:
        response = await async_client.put(
            path, headers=manager, json={"role": "reviewer"}
        )
    assert response.status_code == 409, response.text
    members = await async_client.get(
        f"/v1/libraries/{libraries[0]}/members", headers=manager
    )
    assert members.json()["data"][0]["role"] == "manager"

    organisation_grant = await async_client.put(
        f"/v1/organisations/{organisation}/members/{test_schooladmin_account.id}",
        headers=staff,
        json={"role": "manager"},
    )
    assert organisation_grant.status_code == 200, organisation_grant.text
    removed = await async_client.delete(path, headers=manager)
    assert removed.status_code == 204, removed.text
    assert not removed.content


async def test_concurrent_library_manager_removal_retains_one(
    async_client,
    workspace,
    session,
    test_schooladmin_account,
    test_schooladmin_account_headers,
    test_wrivetedadmin_account_headers,
):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import get_settings
    from app.services.organisation_management import delete_library_member
    from app.services.workspace_errors import WorkspaceConflict

    _, libraries, _ = workspace
    colleague = Educator(
        name="Second local manager",
        email=f"manager-{uuid4()}@example.test",
        school_id=test_schooladmin_account.school_id,
        is_active=True,
    )
    session.add(colleague)
    session.commit()
    engine = create_async_engine(get_settings().SQLALCHEMY_ASYNC_URI)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        for actor in (test_schooladmin_account, colleague):
            granted = await async_client.put(
                f"/v1/libraries/{libraries[0]}/members/{actor.id}",
                headers=test_wrivetedadmin_account_headers,
                json={"role": "manager"},
            )
            assert granted.status_code == 200, granted.text

        async def remove_self(actor):
            async with sessions() as connection:
                try:
                    await delete_library_member(
                        libraries[0], actor.id, connection, actor
                    )
                except WorkspaceConflict:
                    return "retained"
                return "removed"

        results = await asyncio.gather(
            remove_self(test_schooladmin_account),
            remove_self(colleague),
            return_exceptions=True,
        )
        assert all(isinstance(result, str) for result in results), results
        assert sorted(results) == ["removed", "retained"]
        members = await async_client.get(
            f"/v1/libraries/{libraries[0]}/members",
            headers=test_wrivetedadmin_account_headers,
        )
        assert len(members.json()["data"]) == 1
        assert members.json()["data"][0]["role"] == "manager"
    finally:
        await engine.dispose()
        session.delete(colleague)
        session.commit()


async def test_library_details_permission_and_conflict(
    async_client,
    workspace,
    test_wrivetedadmin_account_headers,
    test_schooladmin_account_headers,
):
    _, libraries, _ = workspace
    path = f"/v1/libraries/{libraries[0]}"
    staff = test_wrivetedadmin_account_headers
    before = (await async_client.get(path, headers=staff)).json()
    data = {"name": "Updated library", "expected_name": before["name"]}
    denied = await async_client.patch(
        path, headers=test_schooladmin_account_headers, json=data
    )
    assert denied.status_code in (403, 404), denied.text
    saved = await async_client.patch(path, headers=staff, json=data)
    assert saved.status_code == 200, saved.text
    assert saved.json()["name"] == "Updated library"
    assert saved.json()["organisation_uuid"] == before["organisation_uuid"]
    assert saved.json()["collections"] == before["collections"]
    stale = await async_client.patch(path, headers=staff, json=data)
    assert stale.status_code == 409, stale.text
    assert (await async_client.get(path, headers=staff)).json()[
        "name"
    ] == "Updated library"


async def test_free_organisation_allows_one_library_but_not_two(
    async_client, test_wrivetedadmin_account_headers, session
):
    staff = test_wrivetedadmin_account_headers
    created = await async_client.post(
        "/v1/organisations",
        headers=staff,
        json={"name": "Free entitlement test", "kind": "school"},
    )
    assert created.status_code == 201, created.text
    organisation_id = created.json()["id"]
    try:
        path = f"/v1/organisations/{organisation_id}/libraries"
        first = await async_client.post(
            path, headers=staff, json={"name": "First", "country_code": "NZL"}
        )
        assert first.status_code == 201, first.text
        second = await async_client.post(
            path, headers=staff, json={"name": "Second", "country_code": "NZL"}
        )
        assert second.status_code == 403, second.text
        assert second.json()["detail"]["feature"] == "multiple_libraries"
        detail = (
            await async_client.get(
                f"/v1/organisations/{organisation_id}", headers=staff
            )
        ).json()
        assert len(detail["libraries"]) == 1
        assert detail["entitlements"] == {
            "multiple_libraries": False,
            "library_limit": 1,
            "library_count": 1,
            "reason": "paid_subscription_required",
        }
    finally:
        session.execute(delete(School).where(School.organisation_id == organisation_id))
        session.execute(delete(Organisation).where(Organisation.id == organisation_id))
        session.commit()


@pytest.mark.parametrize(
    "ineligible", ["unpaid", "expired", "inactive", "complimentary"]
)
async def test_entitlement_lapse_preserves_direct_access_but_stops_central_writes(
    async_client,
    workspace,
    session,
    test_schooladmin_account,
    test_schooladmin_account_headers,
    test_wrivetedadmin_account_headers,
    ineligible,
):
    organisation, libraries, _ = workspace
    staff, manager = (
        test_wrivetedadmin_account_headers,
        test_schooladmin_account_headers,
    )
    grant = await async_client.put(
        f"/v1/organisations/{organisation}/members/{test_schooladmin_account.id}",
        headers=staff,
        json={"role": "manager"},
    )
    assert grant.status_code == 200, grant.text
    direct = await async_client.put(
        f"/v1/libraries/{libraries[0]}/members/{test_schooladmin_account.id}",
        headers=staff,
        json={"role": "manager"},
    )
    assert direct.status_code == 200, direct.text
    before = await async_client.get(f"/v1/libraries/{libraries[1]}", headers=manager)
    assert "catalogue_write" in before.json()["capabilities"]
    subscription = session.get(Subscription, f"sub_workspace_{organisation}")
    if ineligible == "unpaid":
        subscription.paid_at = None
        subscription.stripe_status = "active"
        subscription.collection_method = "send_invoice"
    elif ineligible == "expired":
        subscription.expiration = datetime.utcnow() - timedelta(seconds=1)
    elif ineligible == "inactive":
        subscription.is_active = False
    else:
        subscription.stripe_customer_id = ""
        subscription.info = {"source": "staff_grant"}
    session.commit()
    after = await async_client.get(f"/v1/libraries/{libraries[1]}", headers=manager)
    assert after.status_code == 200
    assert after.json()["capabilities"] == ["catalogue_read", "revoke_members"]
    denied = await async_client.post(
        f"/v1/libraries/{libraries[1]}/collections",
        headers=manager,
        json={"name": "Blocked"},
    )
    assert denied.status_code == 403
    retained = await async_client.get(f"/v1/libraries/{libraries[0]}", headers=manager)
    assert "catalogue_write" in retained.json()["capabilities"]
    expansion = await async_client.post(
        f"/v1/organisations/{organisation}/libraries",
        headers=staff,
        json={"name": "No bypass", "country_code": "NZL"},
    )
    assert expansion.status_code == 403
    detail = (
        await async_client.get(f"/v1/organisations/{organisation}", headers=manager)
    ).json()
    assert not detail["entitlements"]["multiple_libraries"]
    assert len(detail["libraries"]) == 2


async def test_billing_library_endpoint_is_removed(
    async_client,
    workspace,
    test_wrivetedadmin_account_headers,
):
    organisation, libraries, _ = workspace
    path = f"/v1/organisations/{organisation}/billing-library"
    for method in ("GET", "PUT"):
        response = await async_client.request(
            method,
            path,
            headers=test_wrivetedadmin_account_headers,
            **({"json": {"library_uuid": libraries[0]}} if method == "PUT" else {}),
        )
        assert response.status_code == 404


async def test_attaching_paid_library_never_assigns_subscription_ownership(
    async_client,
    workspace,
    session,
    test_wrivetedadmin_account_headers,
):
    organisation, libraries, _ = workspace
    session.delete(
        session.get(OrganisationSubscription, f"sub_workspace_{organisation}")
    )
    paid_library = session.scalar(
        select(School).where(School.school_uuid == libraries[0])
    )
    paid_library.organisation_id = None
    session.commit()
    path = f"/v1/organisations/{organisation}/libraries/{libraries[0]}/attach"
    for suffix in ("", "?billing_sponsor=true"):
        rejected = await async_client.post(
            path + suffix, headers=test_wrivetedadmin_account_headers
        )
        assert rejected.status_code == 403, rejected.text
        session.refresh(paid_library)
        assert paid_library.organisation_id is None
        assert (
            session.get(OrganisationSubscription, f"sub_workspace_{organisation}")
            is None
        )
    paid_library.organisation_id = organisation
    session.commit()


async def test_concurrent_free_library_admission_has_one_winner(
    async_client,
    session,
    test_wrivetedadmin_account,
    test_wrivetedadmin_account_headers,
):
    from uuid import UUID

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import get_settings
    from app.schemas.organisation import LibraryCreate
    from app.services.organisation_management import create_library
    from app.services.workspace_errors import WorkspaceForbidden

    response = await async_client.post(
        "/v1/organisations",
        headers=test_wrivetedadmin_account_headers,
        json={"name": "Concurrent free admission", "kind": "school"},
    )
    assert response.status_code == 201
    organisation_id = UUID(response.json()["id"])
    engine = create_async_engine(get_settings().SQLALCHEMY_ASYNC_URI)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def admit(name):
        async with sessions() as connection:
            try:
                await create_library(
                    organisation_id,
                    LibraryCreate(name=name, country_code="NZL"),
                    connection,
                    test_wrivetedadmin_account,
                )
                return 201
            except WorkspaceForbidden as error:
                assert error.detail["code"] == "entitlement_required"
                return 403

    try:
        assert sorted(
            await asyncio.gather(admit("First contender"), admit("Second contender"))
        ) == [201, 403]
    finally:
        await engine.dispose()
        session.execute(delete(School).where(School.organisation_id == organisation_id))
        session.execute(delete(Organisation).where(Organisation.id == organisation_id))
        session.commit()


async def test_library_attachment_does_not_replace_organisation_subscription(
    async_client,
    workspace,
    session,
    test_wrivetedadmin_account_headers,
):
    organisation, libraries, _ = workspace
    incoming = session.scalar(select(School).where(School.school_uuid == libraries[1]))
    incoming.organisation_id = None
    session.commit()
    response = await async_client.post(
        f"/v1/organisations/{organisation}/libraries/{libraries[1]}/attach",
        headers=test_wrivetedadmin_account_headers,
    )
    assert response.status_code == 200, response.text
    session.refresh(incoming)
    assert str(incoming.organisation_id) == organisation
    assert (
        str(
            session.get(
                OrganisationSubscription, f"sub_workspace_{organisation}"
            ).organisation_id
        )
        == organisation
    )


async def test_newer_unpaid_subscription_does_not_hide_current_paid_subscription(
    async_client, workspace, session, test_product, test_wrivetedadmin_account_headers
):
    organisation, libraries, _ = workspace
    session.add(
        Subscription(
            id=f"sub_unpaid_workspace_{organisation}",
            school_id=libraries[0],
            type=SubscriptionType.SCHOOL,
            stripe_customer_id="cus_unpaid_workspace",
            is_active=True,
            paid_at=None,
            expiration=datetime.utcnow() + timedelta(days=90),
            product_id=test_product.id,
        )
    )
    session.flush()
    session.add(
        OrganisationSubscription(
            organisation_id=organisation,
            subscription_id=f"sub_unpaid_workspace_{organisation}",
        )
    )
    session.commit()
    detail = await async_client.get(
        f"/v1/organisations/{organisation}", headers=test_wrivetedadmin_account_headers
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["entitlements"]["multiple_libraries"]


async def test_subscription_ownership_survives_library_reorganisation(
    async_client,
    workspace,
    session,
    test_wrivetedadmin_account_headers,
):
    organisation, libraries, _ = workspace
    library = session.scalar(select(School).where(School.school_uuid == libraries[0]))
    library.organisation_id = None
    session.commit()
    try:
        detail = await async_client.get(
            f"/v1/organisations/{organisation}",
            headers=test_wrivetedadmin_account_headers,
        )
        assert detail.status_code == 200
        assert detail.json()["entitlements"]["multiple_libraries"]
    finally:
        library.organisation_id = organisation
        session.commit()


def test_subscription_assignment_is_explicit_idempotent_and_cannot_transfer(
    workspace, session
):
    from uuid import UUID

    from scripts.associate_organisation_subscription import associate

    organisation, _, _ = workspace
    subscription_id = f"sub_workspace_{organisation}"
    session.delete(session.get(OrganisationSubscription, subscription_id))
    session.flush()
    assert associate(session, UUID(organisation), subscription_id)
    assert not associate(session, UUID(organisation), subscription_id)
    other = Organisation(name="Another owner", kind="school")
    session.add(other)
    session.flush()
    try:
        with pytest.raises(ValueError, match="already belongs"):
            associate(session, other.id, subscription_id)
        assert (
            str(session.get(OrganisationSubscription, subscription_id).organisation_id)
            == organisation
        )
    finally:
        session.delete(other)
        session.commit()


async def test_family_subscription_cannot_unlock_organisation(
    async_client,
    workspace,
    session,
    test_wrivetedadmin_account_headers,
):
    from uuid import UUID

    from scripts.associate_organisation_subscription import associate

    organisation, _, _ = workspace
    subscription_id = f"sub_workspace_{organisation}"
    subscription = session.get(Subscription, subscription_id)
    subscription.type = SubscriptionType.FAMILY
    session.commit()
    with pytest.raises(ValueError, match="school/library"):
        associate(session, UUID(organisation), subscription_id)
    session.rollback()
    detail = await async_client.get(
        f"/v1/organisations/{organisation}", headers=test_wrivetedadmin_account_headers
    )
    assert not detail.json()["entitlements"]["multiple_libraries"]


@pytest.fixture
def workspace(session, test_school, test_product):
    organisation = Organisation(name=f"Workspace {uuid4()}", kind="school")
    session.add(organisation)
    session.flush()
    libraries = [
        School(
            name=f"Division {index}",
            country_code=test_school.country_code,
            organisation_id=organisation.id,
            state=SchoolState.INACTIVE,
            info={"location": {}},
        )
        for index in range(2)
    ]
    session.add_all(libraries)
    session.flush()
    collections = [
        Collection(name="Main", school_id=library.school_uuid, is_default=True)
        for library in libraries
    ]
    session.add_all(collections)
    session.add(
        Subscription(
            id=f"sub_workspace_{organisation.id}",
            school_id=libraries[0].school_uuid,
            type=SubscriptionType.SCHOOL,
            stripe_customer_id="cus_workspace_test",
            is_active=True,
            paid_at=datetime.utcnow(),
            expiration=datetime.utcnow() + timedelta(days=30),
            product_id=test_product.id,
        )
    )
    session.flush()
    session.add(
        OrganisationSubscription(
            organisation_id=organisation.id,
            subscription_id=f"sub_workspace_{organisation.id}",
        )
    )
    session.commit()
    identifiers = (
        str(organisation.id),
        [str(library.school_uuid) for library in libraries],
        [str(collection.id) for collection in collections],
    )
    yield identifiers
    session.rollback()
    session.execute(delete(School).where(School.organisation_id == organisation.id))
    session.execute(delete(Organisation).where(Organisation.id == organisation.id))
    session.commit()


async def test_manager_membership_is_live_and_does_not_expand_legacy_authority(
    async_client,
    workspace,
    test_schooladmin_account,
    test_schooladmin_account_headers,
    test_wrivetedadmin_account_headers,
):
    organisation, libraries, _ = workspace
    org_path = f"/v1/organisations/{organisation}"
    manager = test_schooladmin_account_headers
    staff = test_wrivetedadmin_account_headers
    assert (await async_client.get(org_path, headers=manager)).status_code == 404
    grant = await async_client.post(
        f"{org_path}/members",
        headers=staff,
        json={"email": test_schooladmin_account.email.upper(), "role": "manager"},
    )
    assert grant.status_code == 200, grant.text
    response = await async_client.get(org_path, headers=manager)
    assert response.status_code == 200, response.text
    assert len(response.json()["libraries"]) == 2
    assert response.json()["can_manage"]
    assert (
        await async_client.get(f"/v1/school/{libraries[0]}", headers=manager)
    ).status_code == 403
    assert (
        await async_client.post(
            f"/v1/organisations/{organisation}/libraries/{libraries[0]}/attach",
            headers=manager,
        )
    ).status_code == 403
    last = await async_client.delete(
        f"{org_path}/members/{test_schooladmin_account.id}", headers=manager
    )
    assert last.status_code == 409


async def test_library_reviewer_scope_revocation_and_view_as(
    async_client,
    workspace,
    test_schooladmin_account,
    test_schooladmin_account_headers,
    test_wrivetedadmin_account_headers,
):
    organisation, libraries, collections = workspace
    library, sibling = libraries
    staff = test_wrivetedadmin_account_headers
    member = test_schooladmin_account_headers
    grant_path = f"/v1/libraries/{library}/members/{test_schooladmin_account.id}"
    assert (
        await async_client.put(grant_path, headers=staff, json={"role": "reviewer"})
    ).status_code == 200
    response = await async_client.get(
        f"/v1/organisations/{organisation}", headers=member
    )
    assert response.status_code == 200, response.text
    assert [entry["library_uuid"] for entry in response.json()["libraries"]] == [
        library
    ]
    assert (
        await async_client.get(f"/v1/libraries/{sibling}", headers=member)
    ).status_code == 404
    assert (
        await async_client.get(
            f"/v1/libraries/{library}/collections/{collections[1]}/items",
            headers=member,
        )
    ).status_code == 404
    assert (
        await async_client.post(
            f"/v1/libraries/{library}/collections",
            headers=member,
            json={"name": "Denied"},
        )
    ).status_code == 403
    view = await async_client.post(
        f"/v1/auth/view-as/{test_schooladmin_account.id}", headers=staff
    )
    assert view.status_code == 200, view.text
    view_headers = {**staff, "X-View-As": view.json()["context"]}
    viewed = await async_client.get(f"/v1/libraries/{library}", headers=view_headers)
    assert viewed.status_code == 200, viewed.text
    assert "catalogue_write" not in viewed.json()["capabilities"]
    assert (
        await async_client.post(
            f"/v1/libraries/{library}/collections",
            headers=view_headers,
            json={"name": "Denied"},
        )
    ).status_code == 403
    assert (await async_client.delete(grant_path, headers=staff)).status_code == 204
    assert (
        await async_client.get(f"/v1/libraries/{library}", headers=member)
    ).status_code == 404


async def test_explicit_import_preview_is_additive_and_collection_local(
    async_client, workspace, test_wrivetedadmin_account_headers, session
):
    _, libraries, collections = workspace
    staff = test_wrivetedadmin_account_headers
    library = libraries[0]
    created = await async_client.post(
        f"/v1/libraries/{library}/collections",
        headers=staff,
        json={"name": "High school import"},
    )
    assert created.status_code == 201, created.text
    collection = created.json()["id"]
    assert not created.json()["is_default"]
    path = f"/v1/libraries/{library}/collections/{collection}"
    payload = {
        "items": [
            {
                "edition_isbn": "0140328726",
                "title": "Local catalogue title",
                "copies_total": 2,
            }
        ]
    }
    preview = await async_client.post(f"{path}/import", headers=staff, json=payload)
    assert preview.status_code == 200, preview.text
    assert preview.json()["dry_run"] and preview.json()["added"] == 1
    assert (await async_client.get(f"{path}/items", headers=staff)).json()["total"] == 0
    payload["dry_run"] = False
    applied = await async_client.post(f"{path}/import", headers=staff, json=payload)
    assert applied.status_code == 200, applied.text
    repeated = await async_client.post(f"{path}/import", headers=staff, json=payload)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["added"] == 0 and repeated.json()["updated"] == 1
    items = (await async_client.get(f"{path}/items", headers=staff)).json()
    assert items["total"] == 1
    assert items["data"][0]["edition_isbn"] == "9780140328721"
    assert items["data"][0]["copies_total"] == 2
    assert items["data"][0]["title"] == "Local catalogue title"
    assert (
        await async_client.get(
            f"/v1/libraries/{library}/collections/{collections[0]}/items", headers=staff
        )
    ).json()["total"] == 0
    assert (
        session.scalar(
            select(Collection.is_default).where(Collection.id == collections[0])
        )
        is True
    )


async def test_student_and_anonymous_have_no_workspace_access(
    async_client, workspace, test_student_user_account_headers
):
    organisation, libraries, _ = workspace
    assert (await async_client.get("/v1/organisations")).status_code in (401, 403)
    headers = test_student_user_account_headers
    assert (await async_client.get("/v1/organisations", headers=headers)).json()[
        "data"
    ] == []
    assert (
        await async_client.get(f"/v1/organisations/{organisation}", headers=headers)
    ).status_code == 404
    assert (
        await async_client.get(f"/v1/libraries/{libraries[0]}", headers=headers)
    ).status_code == 404


async def test_import_preserves_unavailable_copies_when_total_changes(
    async_client, workspace, test_wrivetedadmin_account_headers, session
):
    _, libraries, collections = workspace
    path = f"/v1/libraries/{libraries[0]}/collections/{collections[0]}/import"
    staff = test_wrivetedadmin_account_headers
    isbn = generate_random_valid_isbn13()

    async def import_copies(total):
        response = await async_client.post(
            path,
            headers=staff,
            json={
                "dry_run": False,
                "items": [{"edition_isbn": isbn, "copies_total": total}],
            },
        )
        assert response.status_code == 200, response.text
        session.rollback()
        return session.execute(
            select(CollectionItem.copies_total, CollectionItem.copies_available).where(
                CollectionItem.collection_id == collections[0],
                CollectionItem.edition_isbn == isbn,
            )
        ).one()

    assert tuple(await import_copies(0)) == (0, 0)
    assert tuple(await import_copies(5)) == (5, 5)
    session.execute(
        CollectionItem.__table__.update()
        .where(
            CollectionItem.collection_id == collections[0],
            CollectionItem.edition_isbn == isbn,
        )
        .values(copies_available=2)
    )
    session.commit()
    assert tuple(await import_copies(8)) == (8, 5)
    assert tuple(await import_copies(8)) == (8, 5)
    assert tuple(await import_copies(1)) == (1, 0)
    assert tuple(await import_copies(0)) == (0, 0)
    assert tuple(await import_copies(2)) == (2, 2)
    other_path = f"/v1/libraries/{libraries[0]}/collections/{collections[1]}/import"
    mismatch = await async_client.post(
        other_path, headers=staff, json={"items": [{"edition_isbn": isbn}]}
    )
    assert mismatch.status_code == 404


async def test_last_active_organisation_manager_cannot_rely_on_inactive_grant(
    async_client,
    workspace,
    test_schooladmin_account,
    test_schooladmin_account_headers,
    test_wrivetedadmin_account_headers,
    session,
):
    organisation, _, _ = workspace
    staff = test_wrivetedadmin_account_headers
    member = test_schooladmin_account_headers
    path = f"/v1/organisations/{organisation}/members"
    granted = await async_client.put(
        f"{path}/{test_schooladmin_account.id}", headers=staff, json={"role": "manager"}
    )
    assert granted.status_code == 200
    inactive = Educator(
        name="Inactive organisation manager",
        email=f"inactive-{uuid4()}@example.test",
        school_id=test_schooladmin_account.school_id,
        is_active=False,
    )
    session.add(inactive)
    session.flush()
    inactive_id = inactive.id
    session.add(
        OrganisationMembership(organisation_id=organisation, user_id=inactive_id)
    )
    session.commit()
    try:
        last_active = await async_client.delete(
            f"{path}/{test_schooladmin_account.id}", headers=member
        )
        assert last_active.status_code == 409, last_active.text
        remove_inactive = await async_client.delete(
            f"{path}/{inactive_id}", headers=member
        )
        assert remove_inactive.status_code == 204, remove_inactive.text
    finally:
        session.rollback()
        session.delete(inactive)
        session.commit()


async def test_new_library_has_default_collection_and_payload_cannot_reassign_owner(
    async_client, workspace, test_wrivetedadmin_account_headers, test_school
):
    organisation, libraries, _ = workspace
    staff = test_wrivetedadmin_account_headers
    created = await async_client.post(
        f"/v1/organisations/{organisation}/libraries",
        headers=staff,
        json={"name": "New high school", "country_code": test_school.country_code},
    )
    assert created.status_code == 201, created.text
    library = created.json()
    assert library["organisation_uuid"] == organisation
    assert len(library["collections"]) == 1
    assert library["collections"][0]["is_default"] is True
    malicious = await async_client.post(
        f"/v1/libraries/{library['library_uuid']}/collections",
        headers=staff,
        json={"name": "Wrong owner", "school_id": libraries[0], "is_default": True},
    )
    assert malicious.status_code == 422
    wrong_country = await async_client.post(
        f"/v1/organisations/{organisation}/libraries",
        headers=staff,
        json={"name": "Should not exist", "country_code": "ZZZ"},
    )
    assert wrong_country.status_code == 422
    after = await async_client.get(f"/v1/organisations/{organisation}", headers=staff)
    assert len(after.json()["libraries"]) == 3


async def test_member_grants_reject_student_and_foreign_manager_escalation(
    async_client,
    workspace,
    test_schooladmin_account,
    test_schooladmin_account_headers,
    test_student_user_account_headers,
    test_wrivetedadmin_account_headers,
):
    organisation, libraries, _ = workspace
    staff = test_wrivetedadmin_account_headers
    member = test_schooladmin_account_headers
    student = (
        await async_client.get("/v1/auth/me", headers=test_student_user_account_headers)
    ).json()
    rejected = await async_client.put(
        f"/v1/libraries/{libraries[0]}/members/{student['user']['id']}",
        headers=staff,
        json={"role": "manager"},
    )
    assert rejected.status_code == 422, rejected.text
    granted = await async_client.put(
        f"/v1/libraries/{libraries[0]}/members/{test_schooladmin_account.id}",
        headers=staff,
        json={"role": "manager"},
    )
    assert granted.status_code == 200
    denied = await async_client.put(
        f"/v1/organisations/{organisation}/members/{test_schooladmin_account.id}",
        headers=member,
        json={"role": "manager"},
    )
    assert denied.status_code == 404


@pytest.fixture
def workspace_books(workspace, session):
    _, _, collections = workspace
    works = [Work(title=f"Library book {uuid4()}") for _ in collections]
    session.add_all(works)
    session.flush()
    editions = [
        Edition(isbn=generate_random_valid_isbn13(), work_id=work.id) for work in works
    ]
    session.add_all(editions)
    session.flush()
    session.add_all(
        [
            CollectionItem(collection_id=collection, edition_isbn=edition.isbn)
            for collection, edition in zip(collections, editions, strict=True)
        ]
    )
    session.commit()
    work_ids = [work.id for work in works]
    isbns = [edition.isbn for edition in editions]
    yield work_ids
    session.rollback()
    session.execute(
        delete(CollectionItem).where(CollectionItem.edition_isbn.in_(isbns))
    )
    session.execute(delete(Edition).where(Edition.isbn.in_(isbns)))
    session.execute(delete(Work).where(Work.id.in_(work_ids)))
    session.commit()


async def test_library_reading_uses_requested_library_not_home_school(
    async_client,
    workspace,
    workspace_books,
    test_schooladmin_account,
    test_schooladmin_account_headers,
    test_wrivetedadmin_account_headers,
):
    organisation, libraries, _ = workspace
    staff = test_wrivetedadmin_account_headers
    manager = test_schooladmin_account_headers
    granted = await async_client.put(
        f"/v1/organisations/{organisation}/members/{test_schooladmin_account.id}",
        headers=staff,
        json={"role": "manager"},
    )
    assert granted.status_code == 200
    for library, work_id in zip(libraries, workspace_books, strict=True):
        queue = await async_client.get(
            f"/v1/libraries/{library}/review-queue", headers=manager
        )
        assert queue.status_code == 200, queue.text
        assert [item["work_id"] for item in queue.json()["data"]] == [work_id]
        assert queue.headers["cache-control"] == "private, no-store"
        insights = await async_client.get(
            f"/v1/libraries/{library}/insights", headers=manager
        )
        assert insights.status_code == 200, insights.text
        assert insights.json()["school_uuid"] == library
        assert insights.json()["collection"]["works"] == 1
        assert insights.json()["availability"]["interests"] == "privacy_filtered"
        assert insights.headers["cache-control"] == "private, no-store"
        legacy = await async_client.get(
            f"/v1/school/{library}/insights", headers=manager
        )
        assert legacy.status_code == 403


async def test_library_reviewer_reading_denies_sibling_and_supports_view_as(
    async_client,
    workspace,
    workspace_books,
    test_schooladmin_account,
    test_schooladmin_account_headers,
    test_wrivetedadmin_account_headers,
):
    _, libraries, _ = workspace
    staff = test_wrivetedadmin_account_headers
    reviewer = test_schooladmin_account_headers
    for suffix in ("insights", "review-queue"):
        assert (
            await async_client.get(
                f"/v1/libraries/{libraries[0]}/{suffix}", headers=reviewer
            )
        ).status_code == 404
    granted = await async_client.put(
        f"/v1/libraries/{libraries[0]}/members/{test_schooladmin_account.id}",
        headers=staff,
        json={"role": "reviewer"},
    )
    assert granted.status_code == 200
    view = await async_client.post(
        f"/v1/auth/view-as/{test_schooladmin_account.id}", headers=staff
    )
    assert view.status_code == 200
    view_headers = {**staff, "X-View-As": view.json()["context"]}
    for suffix in ("insights", "review-queue"):
        allowed = await async_client.get(
            f"/v1/libraries/{libraries[0]}/{suffix}", headers=reviewer
        )
        assert allowed.status_code == 200, allowed.text
        viewed = await async_client.get(
            f"/v1/libraries/{libraries[0]}/{suffix}", headers=view_headers
        )
        assert viewed.status_code == 200, viewed.text
        assert (
            await async_client.get(
                f"/v1/libraries/{libraries[1]}/{suffix}", headers=reviewer
            )
        ).status_code == 404
        assert (
            await async_client.get(
                f"/v1/libraries/{libraries[1]}/{suffix}", headers=view_headers
            )
        ).status_code == 404
    assert (
        await async_client.get(
            f"/v1/libraries/{libraries[0]}/review-queue?limit=101", headers=reviewer
        )
    ).status_code == 422
    assert (
        await async_client.get(
            f"/v1/libraries/{libraries[0]}/insights?weeks=1", headers=reviewer
        )
    ).status_code == 422

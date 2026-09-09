from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from app.config import get_settings
from app.models.collection import Collection
from app.models.organisation import Organisation
from app.models.school import School, SchoolState
from app.models.user import User
from app.repositories.organisation_repository import (
    HoldingUpdate,
    organisation_repository,
)
from app.schemas.organisation import (
    CollectionCreate,
    CollectionImport,
    LibraryCreate,
    LibraryDetailsUpdate,
    MemberByEmail,
    MemberChange,
    OrganisationCreate,
    OrganisationList,
    OrganisationSummary,
)
from app.services.organisation_entitlements import (
    require_library_capacity,
    resolve_organisation_entitlements,
)
from app.services.organisation_workspace import (
    ELIGIBLE_ROLES,
    LibraryAccess,
    is_platform_staff,
    library_summaries,
    library_summary,
    manages_organisation,
    organisation_detail,
    require_organisation_manager,
    resolve_collection,
    resolve_library,
    workspace_scope,
)
from app.services.workspace_errors import (
    WorkspaceConflict,
    WorkspaceForbidden,
    WorkspaceInvalid,
    WorkspaceNotFound,
)

logger = get_logger()


def require_staff(actor: User):
    if not is_platform_staff(actor):
        raise WorkspaceForbidden("Platform staff permission required")


async def list_organisations(session: AsyncSession, actor: User):
    if not actor.is_active or (
        actor.type not in ELIGIBLE_ROLES and not is_platform_staff(actor)
    ):
        return OrganisationList(data=[])
    organisations = await organisation_repository.list_organisations(
        session, workspace_scope(actor)
    )
    managed = await organisation_repository.managed_organisation_ids(session, actor.id)
    entitlements = await resolve_organisation_entitlements(
        session, {item.id for item in organisations}
    )
    return OrganisationList(
        data=[
            OrganisationSummary(
                id=organisation.id,
                name=organisation.name,
                kind=organisation.kind,
                can_manage=is_platform_staff(actor) or organisation.id in managed,
                entitlements=entitlements[organisation.id],
            )
            for organisation in organisations
        ]
    )


async def create_organisation(
    data: OrganisationCreate, session: AsyncSession, actor: User
):
    require_staff(actor)
    organisation = Organisation(**data.model_dump())
    session.add(organisation)
    await session.flush()
    identifier = organisation.id
    await session.commit()
    logger.info(
        "Organisation created", actor_id=str(actor.id), organisation_id=str(identifier)
    )
    return await organisation_detail(session, actor, identifier)


async def get_organisation(organisation_uuid: UUID, session: AsyncSession, actor: User):
    return await organisation_detail(session, actor, organisation_uuid)


async def create_library(
    organisation_uuid: UUID, data: LibraryCreate, session: AsyncSession, actor: User
):
    await require_organisation_manager(session, actor, organisation_uuid, lock=True)
    await require_library_capacity(session, organisation_uuid)
    if not await organisation_repository.country_exists(session, data.country_code):
        raise WorkspaceInvalid("Unknown country code")
    library = School(
        name=data.name,
        country_code=data.country_code,
        organisation_id=organisation_uuid,
        state=SchoolState.INACTIVE,
        info={"location": {}},
    )
    session.add(library)
    await session.flush()
    identifier = library.school_uuid
    session.add(
        Collection(name=data.collection_name, school_id=identifier, is_default=True)
    )
    await session.commit()
    logger.info(
        "Library created",
        actor_id=str(actor.id),
        library_uuid=str(identifier),
        organisation_id=str(organisation_uuid),
    )
    return await library_summary(
        session, await resolve_library(session, actor, identifier)
    )


async def attach_library(
    organisation_uuid: UUID, library_uuid: UUID, session: AsyncSession, actor: User
):
    require_staff(actor)
    await require_organisation_manager(session, actor, organisation_uuid, lock=True)
    library = await organisation_repository.get_library(
        session, library_uuid, lock=True
    )
    if library is None:
        raise WorkspaceNotFound("Library not found")
    if library.organisation_id is not None:
        raise WorkspaceConflict("Library already belongs to an organisation")
    library.organisation_id = organisation_uuid
    await session.flush()
    await require_library_capacity(session, organisation_uuid, additional_libraries=0)
    await session.commit()
    logger.info(
        "Library attached",
        actor_id=str(actor.id),
        library_uuid=str(library_uuid),
        organisation_id=str(organisation_uuid),
    )
    return await library_summary(
        session, await resolve_library(session, actor, library_uuid)
    )


async def list_libraries(
    session: AsyncSession,
    actor: User,
    skip: int = 0,
    limit: int = 50,
    q: str | None = None,
    organisation_uuid: UUID | None = None,
    standalone: bool = False,
):
    if not actor.is_active or (
        actor.type not in ELIGIBLE_ROLES and not is_platform_staff(actor)
    ):
        return {"data": [], "total": 0, "skip": skip, "limit": limit}
    libraries, total = await organisation_repository.list_libraries(
        session,
        workspace_scope(actor),
        skip=skip,
        limit=limit,
        q=q,
        organisation_uuid=organisation_uuid,
        standalone=standalone,
    )
    return {
        "data": await library_summaries(session, actor, list(libraries)),
        "total": total,
        "skip": skip,
        "limit": limit,
    }


async def get_library(library_uuid: UUID, session: AsyncSession, actor: User):
    return await library_summary(
        session, await resolve_library(session, actor, library_uuid)
    )


async def update_library_details(
    library_uuid: UUID, data: LibraryDetailsUpdate, session: AsyncSession, actor: User
):
    access = await resolve_library(session, actor, library_uuid, "manage_details")
    library = await organisation_repository.get_library(
        session, library_uuid, lock=True
    )
    if library is None:
        raise WorkspaceNotFound("Library not found")
    if library.name != data.expected_name:
        raise WorkspaceConflict("Library details changed. Refresh before saving again.")
    library.name = data.name
    await session.commit()
    logger.info(
        "Library details updated",
        actor_id=str(actor.id),
        library_uuid=str(library_uuid),
    )
    return await library_summary(session, access)


async def list_collections(library_uuid: UUID, session: AsyncSession, actor: User):
    summary = await library_summary(
        session, await resolve_library(session, actor, library_uuid)
    )
    return {"data": summary.collections}


async def create_collection(
    library_uuid: UUID, data: CollectionCreate, session: AsyncSession, actor: User
):
    await resolve_library(session, actor, library_uuid, "catalogue_write")
    await organisation_repository.get_library(session, library_uuid, lock=True)
    count = await organisation_repository.count_collections(session, library_uuid)
    if count and not get_settings().MULTIPLE_COLLECTIONS_ENABLED:
        raise WorkspaceConflict("Additional collections are not enabled yet")
    if count >= 100:
        raise WorkspaceConflict("A library supports up to 100 collections")
    is_default = count == 0
    collection = Collection(
        name=data.name, school_id=library_uuid, is_default=is_default
    )
    session.add(collection)
    await session.flush()
    identifier = collection.id
    await session.commit()
    logger.info(
        "Library collection created",
        actor_id=str(actor.id),
        library_uuid=str(library_uuid),
        collection_id=str(identifier),
    )
    return {
        "id": identifier,
        "name": data.name,
        "is_default": is_default,
        "book_count": 0,
    }


async def list_collection_items(
    library_uuid: UUID,
    collection_uuid: UUID,
    session: AsyncSession,
    actor: User,
    skip: int = 0,
    limit: int = 50,
    q: str | None = None,
):
    access = await resolve_library(session, actor, library_uuid)
    await resolve_collection(session, access.library, collection_uuid)
    rows, total = await organisation_repository.list_holdings(
        session, collection_uuid, skip=skip, limit=limit, q=q
    )
    return {
        "data": [dict(row) for row in rows],
        "total": total,
        "skip": skip,
        "limit": limit,
    }


async def import_collection(
    library_uuid: UUID,
    collection_uuid: UUID,
    data: CollectionImport,
    session: AsyncSession,
    actor: User,
):
    access = await resolve_library(session, actor, library_uuid, "catalogue_write")
    locked_collection = await organisation_repository.get_collection(
        session, access.library.school_uuid, collection_uuid, lock=True
    )
    if locked_collection is None:
        raise WorkspaceNotFound("Collection not found in this library")
    ordered_items = sorted(data.items, key=lambda item: item.edition_isbn)
    isbns = [item.edition_isbn for item in ordered_items]
    existing = await organisation_repository.lock_holdings(
        session, collection_uuid, isbns
    )
    conflicts = [
        {
            "edition_isbn": item.edition_isbn,
            "expected_copies_total": item.expected_copies_total,
            "current_copies_total": existing[item.edition_isbn].copies_total
            if item.edition_isbn in existing
            else None,
        }
        for item in data.items
        if "expected_copies_total" in item.model_fields_set
        and (
            existing[item.edition_isbn].copies_total
            if item.edition_isbn in existing
            else None
        )
        != item.expected_copies_total
    ]
    if conflicts:
        raise WorkspaceConflict(
            {
                "code": "collection_count_conflict",
                "message": "Book counts have changed. Refresh the catalogue and review your changes before saving again.",
                "conflicts": conflicts,
            }
        )
    changes = []
    for item in data.items:
        current = existing.get(item.edition_isbn)
        current_title = current.title if current else None
        changed = current is not None and (
            current.copies_total != item.copies_total
            or (bool(item.title) and item.title != current_title)
        )
        changes.append(
            {
                "edition_isbn": item.edition_isbn,
                "title": item.title or current_title,
                "action": "added"
                if current is None
                else "updated"
                if changed
                else "unchanged",
                "current_copies_total": current.copies_total if current else None,
                "proposed_copies_total": item.copies_total,
            }
        )
    size = await organisation_repository.count_holdings(session, collection_uuid)
    result = {
        "dry_run": data.dry_run,
        "received": len(data.items),
        "added": len(data.items) - len(existing),
        "updated": len(existing),
        "collection_size": size + len(data.items) - len(existing),
        "changes": changes,
    }
    if data.dry_run:
        return result
    await organisation_repository.upsert_holdings(
        session,
        collection_uuid,
        [
            HoldingUpdate(item.edition_isbn, item.copies_total, item.title)
            for item in ordered_items
        ],
    )
    await session.commit()
    logger.info(
        "Library collection imported",
        actor_id=str(actor.id),
        library_uuid=str(library_uuid),
        collection_id=str(collection_uuid),
        rows=len(data.items),
    )
    return result


async def eligible_member(session, user_uuid):
    user = await organisation_repository.get_user(session, user_uuid)
    if user is None or not user.is_active or user.type not in ELIGIBLE_ROLES:
        raise WorkspaceInvalid(
            "Choose an existing active educator or school administrator"
        )
    return user


async def eligible_member_by_email(session, email):
    user = await organisation_repository.find_user_by_email(session, email)
    if user is None or not user.is_active or user.type not in ELIGIBLE_ROLES:
        raise WorkspaceNotFound("No eligible existing staff account matches this email")
    return user


async def add_organisation_member(
    organisation_uuid: UUID, data: MemberByEmail, session: AsyncSession, actor: User
):
    await require_organisation_manager(session, actor, organisation_uuid, lock=True)
    user = await eligible_member_by_email(session, data.email)
    return await put_organisation_member(
        organisation_uuid, user.id, MemberChange(role=data.role), session, actor
    )


async def add_library_member(
    library_uuid: UUID, data: MemberByEmail, session: AsyncSession, actor: User
):
    await resolve_library(session, actor, library_uuid, "manage_members")
    user = await eligible_member_by_email(session, data.email)
    return await put_library_member(
        library_uuid, user.id, MemberChange(role=data.role), session, actor
    )


async def list_organisation_members(
    organisation_uuid: UUID, session: AsyncSession, actor: User
):
    await require_organisation_manager(session, actor, organisation_uuid)
    rows = await organisation_repository.organisation_members(
        session, organisation_uuid
    )
    return {
        "data": [
            {
                "user_id": row["id"],
                "name": row["name"],
                "role": "manager",
                "source": "organisation_membership",
            }
            for row in rows
        ]
    }


async def put_organisation_member(
    organisation_uuid: UUID,
    user_uuid: UUID,
    data: MemberChange,
    session: AsyncSession,
    actor: User,
):
    await require_organisation_manager(session, actor, organisation_uuid, lock=True)
    entitlement = (
        await resolve_organisation_entitlements(session, {organisation_uuid})
    )[organisation_uuid]
    if entitlement.library_count > 1 and (not entitlement.multiple_libraries):
        raise WorkspaceForbidden(
            "A paid subscription is required to grant multi-library management access"
        )
    if data.role != "manager":
        raise WorkspaceInvalid("Organisation membership requires the manager role")
    user = await eligible_member(session, user_uuid)
    name = user.name
    await organisation_repository.grant_organisation_membership(
        session, organisation_uuid, user_uuid
    )
    await session.commit()
    logger.info(
        "Organisation membership granted",
        actor_id=str(actor.id),
        target_id=str(user_uuid),
        organisation_id=str(organisation_uuid),
    )
    return {
        "user_id": user_uuid,
        "name": name,
        "role": "manager",
        "source": "organisation_membership",
    }


async def delete_organisation_member(
    organisation_uuid: UUID, user_uuid: UUID, session: AsyncSession, actor: User
):
    await require_organisation_manager(session, actor, organisation_uuid, lock=True)
    membership = await organisation_repository.organisation_membership(
        session, organisation_uuid, user_uuid
    )
    if membership:
        count = await organisation_repository.active_organisation_manager_count(
            session, organisation_uuid, ELIGIBLE_ROLES
        )
        target = await organisation_repository.get_user(session, user_uuid)
        if (
            count <= 1
            and target is not None
            and target.is_active
            and (target.type in ELIGIBLE_ROLES)
        ):
            raise WorkspaceConflict("Keep at least one organisation manager")
        await session.delete(membership)
        await session.commit()
        logger.info(
            "Organisation membership revoked",
            actor_id=str(actor.id),
            target_id=str(user_uuid),
            organisation_id=str(organisation_uuid),
        )
    return None


async def list_library_members(library_uuid: UUID, session: AsyncSession, actor: User):
    access = await resolve_library(session, actor, library_uuid, "manage_members")
    rows = await organisation_repository.library_members(session, access.library.id)
    return {
        "data": [
            {
                "user_id": row["id"],
                "name": row["name"],
                "role": row["role"],
                "source": "library_membership",
            }
            for row in rows
        ],
        "note": "Existing home-library and organisation permissions are separate and remain in effect.",
    }


async def put_library_member(
    library_uuid: UUID,
    user_uuid: UUID,
    data: MemberChange,
    session: AsyncSession,
    actor: User,
):
    await organisation_repository.get_library(session, library_uuid, lock=True)
    access = await resolve_library(session, actor, library_uuid, "manage_members")
    user = await eligible_member(session, user_uuid)
    name = user.name
    if data.role != "manager":
        await require_library_manager_retained(session, actor, access, user_uuid)
    await organisation_repository.grant_library_membership(
        session, access.library.id, user_uuid, data.role
    )
    await session.commit()
    logger.info(
        "Library membership granted",
        actor_id=str(actor.id),
        target_id=str(user_uuid),
        library_uuid=str(library_uuid),
        role=data.role,
    )
    return {
        "user_id": user_uuid,
        "name": name,
        "role": data.role,
        "source": "library_membership",
    }


async def delete_library_member(
    library_uuid: UUID, user_uuid: UUID, session: AsyncSession, actor: User
):
    await organisation_repository.get_library(session, library_uuid, lock=True)
    access = await resolve_library(session, actor, library_uuid, "manage_members")
    membership = await organisation_repository.library_membership(
        session, access.library.id, user_uuid
    )
    if membership:
        await require_library_manager_retained(session, actor, access, user_uuid)
        await session.delete(membership)
        await session.commit()
        logger.info(
            "Library membership revoked",
            actor_id=str(actor.id),
            target_id=str(user_uuid),
            library_uuid=str(library_uuid),
        )
    return None


async def require_library_manager_retained(
    session: AsyncSession, actor: User, access: LibraryAccess, user_uuid: UUID
) -> None:
    if is_platform_staff(actor):
        return
    if access.library.organisation_id and await manages_organisation(
        session, actor, access.library.organisation_id
    ):
        return
    membership = await organisation_repository.library_membership(
        session, access.library.id, user_uuid
    )
    if membership is None or membership.role != "manager":
        return
    target = await organisation_repository.get_user(session, user_uuid)
    if target is None or not target.is_active or target.type not in ELIGIBLE_ROLES:
        return
    # Removing an explicit grant does not remove a home administrator's authority.
    if await organisation_repository.is_home_library_administrator(
        session, access.library.id, user_uuid
    ):
        return
    if not await organisation_repository.has_other_library_manager(
        session, access.library.id, user_uuid, ELIGIBLE_ROLES
    ):
        raise WorkspaceConflict(
            "Keep at least one library manager, or ask an organisation manager or platform staff to remove this access"
        )

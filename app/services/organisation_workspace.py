from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.collection import Collection
from app.models.organisation import Organisation
from app.models.school import School
from app.models.user import User, UserAccountType
from app.repositories.organisation_repository import (
    LibraryScope,
    organisation_repository,
)
from app.schemas.organisation import (
    CollectionSummary,
    LibrarySummary,
    OrganisationDetail,
)
from app.services.organisation_entitlements import resolve_organisation_entitlements
from app.services.workspace_errors import WorkspaceForbidden, WorkspaceNotFound

READ_CAPABILITIES = frozenset({"catalogue_read", "review", "insights"})
MANAGE_CAPABILITIES = READ_CAPABILITIES | {
    "catalogue_write",
    "manage_members",
    "revoke_members",
    "manage_details",
}
ELIGIBLE_ROLES = {UserAccountType.EDUCATOR, UserAccountType.SCHOOL_ADMIN}
LIBRARY_ROLE_CAPABILITIES = {
    "manager": MANAGE_CAPABILITIES,
    "reviewer": READ_CAPABILITIES,
    "cataloguer": frozenset({"catalogue_read", "catalogue_write"}),
}


def is_platform_staff(actor: User) -> bool:
    return actor.is_active and actor.type == UserAccountType.WRIVETED


def workspace_scope(actor: User) -> LibraryScope:
    return LibraryScope(
        user_id=actor.id,
        home_school_id=getattr(actor, "school_id", None)
        if actor.type in ELIGIBLE_ROLES
        else None,
        unrestricted=is_platform_staff(actor),
    )


async def manages_organisation(
    session: AsyncSession, actor: User, organisation_id: UUID
) -> bool:
    if is_platform_staff(actor):
        return True
    if not actor.is_active or actor.type not in ELIGIBLE_ROLES:
        return False
    return (
        await organisation_repository.organisation_membership(
            session, organisation_id, actor.id
        )
        is not None
    )


@dataclass(frozen=True)
class LibraryAccess:
    library: School
    capabilities: frozenset[str]
    sources: tuple[str, ...]

    def require(self, capability: str) -> None:
        if capability not in self.capabilities:
            raise WorkspaceForbidden(
                "This library action requires additional permission"
            )


def _library_access(
    actor: User,
    library: School,
    *,
    organisation_manager: bool,
    role: str | None,
    organisation_management_enabled: bool = True,
) -> LibraryAccess:
    sources: list[str] = []
    capabilities: frozenset[str] = frozenset()
    if not actor.is_active or (
        actor.type not in ELIGIBLE_ROLES and (not is_platform_staff(actor))
    ):
        return LibraryAccess(library, capabilities, ())
    if is_platform_staff(actor):
        sources.append("platform_staff")
        capabilities |= MANAGE_CAPABILITIES
    if organisation_manager:
        sources.append("organisation_manager")
        capabilities |= (
            MANAGE_CAPABILITIES
            if organisation_management_enabled
            else frozenset({"catalogue_read", "revoke_members"})
        )
    if role:
        sources.append(f"library_{role}")
        capabilities |= LIBRARY_ROLE_CAPABILITIES.get(role, frozenset())
    if actor.type in ELIGIBLE_ROLES and getattr(actor, "school_id", None) == library.id:
        sources.append(
            "legacy_school_admin"
            if actor.type == UserAccountType.SCHOOL_ADMIN
            else "legacy_educator"
        )
        capabilities |= (
            MANAGE_CAPABILITIES
            if actor.type == UserAccountType.SCHOOL_ADMIN
            else READ_CAPABILITIES
        )
    return LibraryAccess(library, capabilities, tuple(sources))


async def library_access(
    session: AsyncSession, actor: User, library: School
) -> LibraryAccess:
    if not actor.is_active or (
        actor.type not in ELIGIBLE_ROLES and (not is_platform_staff(actor))
    ):
        return LibraryAccess(library, frozenset(), ())
    membership = await organisation_repository.library_membership(
        session, library.id, actor.id
    )
    organisation_membership = (
        await organisation_repository.organisation_membership(
            session, library.organisation_id, actor.id
        )
        if library.organisation_id
        else None
    )
    entitlements = await resolve_organisation_entitlements(
        session,
        {library.organisation_id} if organisation_membership is not None else set(),
    )
    entitlement = entitlements.get(library.organisation_id)
    return _library_access(
        actor,
        library,
        organisation_manager=organisation_membership is not None,
        role=membership.role if membership else None,
        organisation_management_enabled=entitlement is None
        or entitlement.library_count <= 1
        or entitlement.multiple_libraries,
    )


async def library_summaries(
    session: AsyncSession, actor: User, libraries: list[School]
) -> list[LibrarySummary]:
    if not libraries:
        return []
    organisation_ids = await organisation_repository.managed_organisation_ids(
        session, actor.id
    )
    roles = await organisation_repository.library_roles(
        session, actor.id, [library.id for library in libraries]
    )
    rows = await organisation_repository.collection_summaries(
        session, [library.school_uuid for library in libraries]
    )
    collections: dict[UUID, list[CollectionSummary]] = {}
    for row in rows:
        collections.setdefault(row["library_uuid"], []).append(
            CollectionSummary(
                id=row["id"],
                name=row["name"],
                is_default=row["is_default"],
                book_count=row["book_count"],
            )
        )
    summaries = []
    entitlements = await resolve_organisation_entitlements(
        session,
        {library.organisation_id for library in libraries if library.organisation_id},
    )
    for library in libraries:
        entitlement = entitlements.get(library.organisation_id)
        access = _library_access(
            actor,
            library,
            organisation_manager=library.organisation_id in organisation_ids,
            role=roles.get(library.id),
            organisation_management_enabled=entitlement is None
            or entitlement.library_count <= 1
            or entitlement.multiple_libraries,
        )
        if access.capabilities:
            summaries.append(
                LibrarySummary(
                    library_uuid=library.school_uuid,
                    name=library.name,
                    organisation_uuid=library.organisation_id,
                    country_code=library.country_code,
                    capabilities=sorted(access.capabilities),
                    access_sources=list(access.sources),
                    collections=collections.get(library.school_uuid, []),
                )
            )
    return summaries


async def resolve_library(
    session: AsyncSession,
    actor: User,
    library_uuid: UUID,
    capability: str = "catalogue_read",
) -> LibraryAccess:
    library = await organisation_repository.get_library(session, library_uuid)
    if library is None:
        raise WorkspaceNotFound("Library not found")
    access = await library_access(session, actor, library)
    if not access.capabilities:
        raise WorkspaceNotFound("Library not found")
    access.require(capability)
    return access


async def resolve_collection(
    session: AsyncSession, library: School, collection_uuid: UUID
) -> Collection:
    collection = await organisation_repository.get_collection(
        session, library.school_uuid, collection_uuid
    )
    if collection is None:
        raise WorkspaceNotFound("Collection not found in this library")
    return collection


async def library_summary(
    session: AsyncSession, access: LibraryAccess
) -> LibrarySummary:
    library = access.library
    collections = await organisation_repository.collection_summaries(
        session, [library.school_uuid]
    )
    return LibrarySummary(
        library_uuid=library.school_uuid,
        name=library.name,
        organisation_uuid=library.organisation_id,
        country_code=library.country_code,
        capabilities=sorted(access.capabilities),
        access_sources=list(access.sources),
        collections=[
            CollectionSummary(
                **{key: value for key, value in row.items() if key != "library_uuid"}
            )
            for row in collections
        ],
    )


async def organisation_detail(
    session: AsyncSession, actor: User, organisation_uuid: UUID
) -> OrganisationDetail:
    if not actor.is_active or (
        actor.type not in ELIGIBLE_ROLES and (not is_platform_staff(actor))
    ):
        raise WorkspaceNotFound("Organisation not found")
    organisation = await organisation_repository.get_organisation(
        session, organisation_uuid
    )
    if organisation is None:
        raise WorkspaceNotFound("Organisation not found")
    manager = await manages_organisation(session, actor, organisation_uuid)
    libraries, _ = await organisation_repository.list_libraries(
        session, workspace_scope(actor), organisation_uuid=organisation_uuid, limit=100
    )
    if not manager and (not libraries):
        raise WorkspaceNotFound("Organisation not found")
    return OrganisationDetail(
        id=organisation.id,
        name=organisation.name,
        kind=organisation.kind,
        can_manage=manager,
        entitlements=(
            await resolve_organisation_entitlements(session, {organisation_uuid})
        )[organisation_uuid],
        libraries=await library_summaries(session, actor, list(libraries)),
    )


async def require_organisation_manager(
    session: AsyncSession, actor: User, organisation_uuid: UUID, *, lock: bool = False
) -> Organisation:
    organisation = await organisation_repository.get_organisation(
        session, organisation_uuid, lock=lock
    )
    if organisation is None or not await manages_organisation(
        session, actor, organisation_uuid
    ):
        raise WorkspaceNotFound("Organisation not found")
    return organisation

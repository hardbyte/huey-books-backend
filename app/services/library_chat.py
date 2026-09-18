from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from app.models.campaign import Campaign
from app.models.school import School, SchoolState
from app.models.user import User
from app.repositories import library_chat as repository
from app.repositories.organisation_repository import organisation_repository
from app.schemas.library_chat import (
    LibraryChatDetail,
    LibraryChatPolicy,
    LibraryChatUpdate,
    pinned_context,
)
from app.services.campaigns import CampaignContext, resolve_campaign
from app.services.organisation_entitlements import resolve_organisation_entitlements
from app.services.organisation_workspace import resolve_library
from app.services.workspace_errors import (
    WorkspaceConflict,
    WorkspaceForbidden,
    WorkspaceNotFound,
)

logger = get_logger()


async def settings_for_library(
    session: AsyncSession, library: School
) -> LibraryChatDetail:
    stored = await repository.get_settings(session, library.school_uuid)
    experiments = (library.info or {}).get("experiments") or {}
    policy = LibraryChatPolicy(
        enabled=stored.enabled if stored else library.state == SchoolState.ACTIVE,
        catalogue_policy=stored.catalogue_policy
        if stored
        else (
            "prefer_library" if library.state == SchoolState.ACTIVE else "library_only"
        ),
        jokes_enabled=stored.jokes_enabled
        if stored
        else not (
            experiments.get("no-jokes") is True or experiments.get("no_jokes") is True
        ),
        spelling_enabled=stored.spelling_enabled if stored else True,
    )
    available = policy.enabled and await reader_access_available(session, library)
    return LibraryChatDetail(
        **policy.model_dump(),
        library_uuid=library.school_uuid,
        name=library.name,
        revision=stored.revision if stored else 0,
        available=available,
        unavailable_reason=None
        if available
        else ("disabled" if not policy.enabled else "subscription_required"),
    )


async def read_settings(
    session: AsyncSession, actor: User, library_uuid: UUID
) -> LibraryChatDetail:
    access = await resolve_library(session, actor, library_uuid)
    return await settings_for_library(session, access.library)


async def update_settings(
    session: AsyncSession, actor: User, library_uuid: UUID, data: LibraryChatUpdate
) -> LibraryChatDetail:
    await resolve_library(session, actor, library_uuid, "manage_details")
    library = await organisation_repository.get_library(
        session, library_uuid, lock=True
    )
    if library is None:
        raise WorkspaceNotFound("Library not found")
    if data.enabled:
        await require_reader_access(session, library)
    stored = await repository.get_settings(session, library_uuid)
    if data.expected_revision != (stored.revision if stored else 0):
        raise WorkspaceConflict(
            "Bookbot settings changed. Refresh before saving again."
        )
    stored = repository.save_settings(
        session, library_uuid, stored, data, data.expected_revision + 1
    )
    await session.commit()
    logger.info(
        "Library chat settings updated",
        actor_id=str(actor.id),
        library_uuid=str(library_uuid),
        revision=stored.revision,
    )
    return await settings_for_library(session, library)


async def select_chat_library(
    session: AsyncSession, library_uuid: UUID | None, home: School | None
) -> tuple[School | None, LibraryChatDetail | None]:
    library = (
        home
        if library_uuid is None
        else await organisation_repository.get_library(session, library_uuid)
    )
    if library is None and library_uuid is None:
        return None, None
    if library is None:
        raise WorkspaceNotFound("Library is not available")
    policy = await settings_for_library(session, library)
    if not policy.enabled:
        raise WorkspaceNotFound("This library’s student chat is not enabled")
    if not policy.available:
        raise WorkspaceForbidden(
            "Student chat requires an active library or a paid organisation subscription"
        )
    return library, policy


@dataclass(frozen=True)
class ChatStartSelection:
    flow_id: UUID | None
    campaign: Campaign | None
    initial_state: dict
    school_id: UUID | None
    library_chat_snapshot: dict | None


async def resolve_start(
    session: AsyncSession,
    *,
    library_uuid: UUID | None,
    flow_id: UUID | None,
    school: School | None,
    initial_state: dict,
) -> ChatStartSelection:
    context = initial_state.get("context", {})
    library, policy = (
        await select_chat_library(session, library_uuid, school)
        if library_uuid is not None
        or context.get("school_wriveted_id")
        or (flow_id is None and school is not None)
        else (None, None)
    )
    snapshot = policy.model_dump(mode="json") if policy else None
    if school is not None:
        initial_state = {
            **initial_state,
            "context": {
                **context,
                "school_wriveted_id": str(school.wriveted_identifier),
                "school_name": school.name,
            },
        }
    initial_state = pinned_context(initial_state, snapshot)
    effective_flow_id = None if policy is not None else flow_id
    campaign = None
    if effective_flow_id is None:
        campaign = await resolve_campaign_for_start(session, library or school)
        if campaign and campaign.flow_id:
            effective_flow_id = campaign.flow_id
    if effective_flow_id is None and library is not None:
        effective_flow_id = await repository.default_flow_id(session)
    if campaign is not None:
        context = dict(initial_state.get("context", {}))
        context["campaign_id"] = str(campaign.id)
        if campaign.booklist_id:
            context["campaign_booklist_id"] = str(campaign.booklist_id)
        initial_state = {**initial_state, "context": context}
    return ChatStartSelection(
        flow_id=effective_flow_id,
        campaign=campaign,
        initial_state=initial_state,
        school_id=school.wriveted_identifier
        if school is not None
        else (library.school_uuid if library else None),
        library_chat_snapshot=snapshot,
    )


async def resolve_campaign_for_start(
    session: AsyncSession, school: School | None
) -> Campaign | None:
    try:
        region_state = None
        if school and isinstance(school.info, dict):
            location = school.info.get("location")
            if isinstance(location, dict):
                region_state = location.get("state")
        context = CampaignContext(
            now=datetime.utcnow(),
            school_id=school.id if school else None,
            country_code=school.country_code if school else None,
            region_state=region_state,
        )
        return await resolve_campaign(session, context)
    except Exception as exc:
        logger.warning("Campaign resolution failed; using default flow", error=str(exc))
        return None


async def require_reader_access(session: AsyncSession, library: School) -> None:
    if not await reader_access_available(session, library):
        raise WorkspaceForbidden(
            "Student chat requires an active library or a paid organisation subscription"
        )


async def reader_access_available(session: AsyncSession, library: School) -> bool:
    if library.state == SchoolState.ACTIVE:
        return True
    if library.organisation_id:
        entitlement = (
            await resolve_organisation_entitlements(session, {library.organisation_id})
        )[library.organisation_id]
        if entitlement.multiple_libraries:
            return True
    return False

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from app.models.school import School, SchoolKind
from app.models.subscription import Subscription
from app.models.user import User, UserAccountType
from app.repositories import organisation_setup as repository
from app.repositories import people as people_repository
from app.repositories.event_repository import event_repository
from app.repositories.organisation_repository import organisation_repository
from app.schemas.organisation_setup import (
    Ineligibility,
    OrganisationSetupInput,
    OrganisationSetupPreview,
    OrganisationSetupResult,
)
from app.schemas.people import PersonBrief, PersonBriefPage
from app.services import idempotency, people
from app.services.organisation_entitlements import require_library_capacity
from app.services.organisation_workspace import (
    MANAGE_CAPABILITIES,
    LibraryAccess,
    is_platform_staff,
    resolve_library,
)
from app.services.school_billing_status import select_paid_subscription
from app.services.workspace_errors import WorkspaceConflict, WorkspaceForbidden

logger = get_logger()

OPERATION = "organisation_setup"

INELIGIBILITY_MESSAGES = {
    "already_grouped": "This library already belongs to an organisation. "
    "Add another library from that organisation.",
    "no_paid_subscription": "Setting up an organisation needs a current paid "
    "school or library subscription on this library.",
    "multiple_paid_subscriptions": "More than one paid subscription applies. "
    "Ask Huey Books support to confirm subscription ownership before continuing.",
    "subscription_already_owned": "This subscription already belongs to an "
    "organisation. Ask Huey Books support to check the existing group.",
}


def _ineligible(code: str) -> Ineligibility:
    return Ineligibility(code=code, message=INELIGIBILITY_MESSAGES[code])


async def source_library(
    db: AsyncSession, actor: User, library_uuid: UUID, *, lock: bool = False
) -> tuple[User, School]:
    """Resolve the library setup starts from, and the authority to do it.

    Assigning subscription ownership is a school-level decision: a library
    manager grant alone is not enough, and View As is rejected upstream because
    setup is not a read route.
    """
    if lock:
        await organisation_repository.get_library(db, library_uuid, lock=True)
        actor = await people_repository.actor_account(db, actor.id)
        if actor is None or not actor.is_active:
            raise WorkspaceForbidden("Your account no longer has access")
    access = await resolve_library(db, actor, library_uuid, "manage_members")
    home_administrator = (
        actor.is_active
        and actor.type == UserAccountType.SCHOOL_ADMIN
        and getattr(actor, "school_id", None) == access.library.id
    )
    if not (is_platform_staff(actor) or home_administrator):
        raise WorkspaceForbidden(
            "Ask a school administrator or Huey Books staff to set up the organisation"
        )
    return actor, access.library


async def eligible_subscription(
    db: AsyncSession, library: School, *, lock: bool = False
) -> tuple[Subscription | None, Ineligibility | None]:
    """Exactly one unowned, currently paid subscription may fund a new group."""
    if library.organisation_id:
        return None, _ineligible("already_grouped")
    now = datetime.now(UTC).replace(tzinfo=None)
    candidates = [
        subscription
        for subscription in await repository.library_subscriptions(
            db, library.school_uuid, lock=lock
        )
        if select_paid_subscription([subscription], now) is not None
    ]
    if not candidates:
        return None, _ineligible("no_paid_subscription")
    if len(candidates) > 1:
        return None, _ineligible("multiple_paid_subscriptions")
    if await repository.subscription_owner(db, candidates[0].id) is not None:
        return None, _ineligible("subscription_already_owned")
    return candidates[0], None


async def preview(
    db: AsyncSession, actor: User, library_uuid: UUID
) -> OrganisationSetupPreview:
    actor, library = await source_library(db, actor, library_uuid)
    _, ineligibility = await eligible_subscription(db, library)
    return OrganisationSetupPreview(
        library_uuid=library.school_uuid,
        library_name=library.name,
        organisation_uuid=library.organisation_id,
        eligible=ineligibility is None,
        ineligibility=ineligibility,
        eligible_manager_count=await repository.eligible_manager_count(db, library.id),
        required_manager_id=None if is_platform_staff(actor) else actor.id,
    )


async def eligible_managers(
    db: AsyncSession, actor: User, library_uuid: UUID, q: str, skip: int, limit: int
) -> PersonBriefPage:
    _, library = await source_library(db, actor, library_uuid)
    rows, total = await repository.eligible_manager_page(db, library.id, q, skip, limit)
    return PersonBriefPage(
        data=[
            PersonBrief(user_id=user.id, name=user.name, email=user.email)
            for user in rows
        ],
        total=total,
        skip=skip,
        limit=limit,
    )


async def complete(
    db: AsyncSession, actor: User, library_uuid: UUID, data: OrganisationSetupInput
) -> OrganisationSetupResult:
    try:
        result = await _complete(db, actor, library_uuid, data)
        await db.commit()
        return result
    except Exception:
        await db.rollback()
        raise


async def _complete(
    db: AsyncSession, actor: User, library_uuid: UUID, data: OrganisationSetupInput
) -> OrganisationSetupResult:
    digest = idempotency.fingerprint(OPERATION, str(library_uuid), data)
    replayed = await idempotency.replay(
        db, OPERATION, data.request_id, actor.id, digest, OrganisationSetupResult
    )
    if replayed is not None:
        return replayed

    actor, library = await source_library(db, actor, library_uuid, lock=True)
    _require_current_review(library, data)
    subscription, ineligibility = await eligible_subscription(db, library, lock=True)
    if ineligibility is not None:
        raise WorkspaceConflict(ineligibility.model_dump())

    managers = await _resolve_managers(db, actor, library, data)
    librarian = await _resolve_librarian(db, data, managers)

    organisation = await repository.create_organisation(
        db,
        data.organisation_name,
        data.organisation_kind,
        subscription.id,
        actor.id,
    )
    library.organisation_id = organisation.id
    await db.flush()
    await require_library_capacity(db, organisation.id)
    new_library = await organisation_repository.create_library(
        db,
        name=data.new_library_name,
        country_code=library.country_code,
        organisation_id=organisation.id,
        collection_name=data.collection_name,
        location=(library.info or {}).get("location"),
    )

    organisation_context = people.PeopleContext(
        organisation.name, organisation.id, None, False, True
    )
    for user_id in data.manager_ids:
        await organisation_repository.grant_organisation_membership(
            db, organisation.id, user_id
        )
        await people.audit(
            db,
            actor,
            organisation_context,
            user_id,
            "added",
            "organisation",
            role="manager",
        )
    if librarian is not None:
        await _grant_librarian(db, actor, organisation, new_library, librarian)

    result = OrganisationSetupResult(
        organisation_uuid=organisation.id,
        existing_library_uuid=library.school_uuid,
        new_library_uuid=new_library.school_uuid,
    )
    idempotency.record(db, OPERATION, data.request_id, actor.id, digest, result)
    await event_repository.acreate(
        db,
        title="Organisation set up from library",
        school=library,
        account=actor,
        commit=False,
        info={
            "organisation_id": str(organisation.id),
            "new_library_uuid": str(new_library.school_uuid),
            "subscription_id": subscription.id,
            "manager_count": len(data.manager_ids),
            "request_id": str(data.request_id),
        },
    )
    logger.info(
        "Organisation set up from library",
        actor_id=str(actor.id),
        organisation_id=str(organisation.id),
        library_uuid=str(library.school_uuid),
        new_library_uuid=str(new_library.school_uuid),
        subscription_id=subscription.id,
        manager_count=len(data.manager_ids),
    )
    return result


def _require_current_review(library: School, data: OrganisationSetupInput) -> None:
    if library.kind is not SchoolKind.SCHOOL:
        raise WorkspaceConflict(
            {
                "code": "not_an_education_unit",
                "message": "Add another library from the organisation this "
                "library already belongs to.",
            }
        )
    if library.name != data.expected_library_name:
        raise WorkspaceConflict(
            {
                "code": "stale_review",
                "message": "The library name changed. Refresh and review the "
                "setup again.",
            }
        )


async def _resolve_managers(
    db: AsyncSession, actor: User, library: School, data: OrganisationSetupInput
) -> dict[UUID, User]:
    if not is_platform_staff(actor) and actor.id not in data.manager_ids:
        raise WorkspaceForbidden(
            "Include yourself as an organisation manager to retain access to "
            "both libraries"
        )
    managers = await repository.selected_managers(db, library.id, data.manager_ids)
    if set(data.manager_ids) - managers.keys():
        raise WorkspaceConflict(
            {
                "code": "manager_unavailable",
                "message": "A chosen colleague can no longer manage this "
                "library. Review the people choices.",
            }
        )
    return managers


async def _resolve_librarian(
    db: AsyncSession, data: OrganisationSetupInput, managers: dict[UUID, User]
) -> dict | None:
    if data.librarian is None:
        return None
    email = str(data.librarian.email)
    await people_repository.lock_email(db, email)
    matches = await people_repository.find_email(db, email)
    if len(matches) > 1:
        raise WorkspaceConflict(
            {
                "code": "ambiguous_email",
                "message": "This email matches multiple accounts. Ask Huey "
                "Books support to resolve it.",
            }
        )
    if matches and matches[0] in managers:
        raise WorkspaceConflict(
            {
                "code": "conflicting_access",
                "message": "This person is already set to manage both "
                "libraries. Choose one access scope.",
            }
        )
    if matches:
        person = await people_repository.person(db, matches[0])
        if not person["is_active"] or person["type"] not in (
            UserAccountType.EDUCATOR,
            UserAccountType.SCHOOL_ADMIN,
        ):
            raise WorkspaceConflict(
                {
                    "code": "librarian_unavailable",
                    "message": "This account cannot be given library access. "
                    "Contact Huey Books staff.",
                }
            )
        return person
    user_id = await people_repository.create_person(db, data.librarian.name, email)
    return await people_repository.person(db, user_id)


async def _grant_librarian(
    db: AsyncSession, actor: User, organisation, new_library: School, librarian: dict
) -> None:
    await organisation_repository.grant_library_membership(
        db, new_library.id, librarian["id"], "manager"
    )
    # The new library has just been created in this transaction, so its access
    # is known without re-resolving it: the context only carries the library
    # for audit attribution and its name for the invitation.
    context = people.PeopleContext(
        new_library.name,
        organisation.id,
        LibraryAccess(new_library, MANAGE_CAPABILITIES, ("organisation_manager",)),
        False,
        True,
    )
    await people.notify(db, context, librarian)
    await people.audit(
        db, actor, context, librarian["id"], "added", "direct", role="manager"
    )

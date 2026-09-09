from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.organisation_repository import organisation_repository
from app.schemas.organisation import OrganisationEntitlements
from app.services.school_billing_status import select_paid_subscription
from app.services.workspace_errors import WorkspaceConflict, WorkspaceForbidden

MULTIPLE_LIBRARIES = "multiple_libraries"
MAX_LIBRARIES = 100


async def resolve_organisation_entitlements(
    session: AsyncSession, organisation_ids: set[UUID]
) -> dict[UUID, OrganisationEntitlements]:
    if not organisation_ids:
        return {}
    counts, subscriptions = await organisation_repository.entitlement_evidence(
        session, organisation_ids
    )
    now = datetime.utcnow()
    results = {}
    for organisation_id in organisation_ids:
        paid = select_paid_subscription(subscriptions.get(organisation_id, []), now)
        results[organisation_id] = OrganisationEntitlements(
            multiple_libraries=paid is not None,
            library_limit=MAX_LIBRARIES if paid else 1,
            library_count=counts.get(organisation_id, 0),
            reason="paid_subscription" if paid else "paid_subscription_required",
        )
    return results


async def require_library_capacity(
    session: AsyncSession, organisation_id: UUID, *, additional_libraries: int = 1
) -> None:
    entitlement = (await resolve_organisation_entitlements(session, {organisation_id}))[
        organisation_id
    ]
    if entitlement.library_count + additional_libraries > entitlement.library_limit:
        if entitlement.multiple_libraries:
            raise WorkspaceConflict("An organisation supports up to 100 libraries")
        raise WorkspaceForbidden(
            {
                "code": "entitlement_required",
                "feature": MULTIPLE_LIBRARIES,
                "message": "Multiple libraries require a current paid organisation subscription.",
            }
        )

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ServiceAccount, User
from app.models.user import UserAccountType
from app.repositories.organisation_repository import organisation_repository
from app.repositories.review_repository import review_repository
from app.services.organisation_workspace import resolve_library
from app.services.workspace_errors import WorkspaceForbidden, WorkspaceNotFound


async def get_legacy_review_queue(
    session: AsyncSession,
    actor: User | ServiceAccount,
    *,
    school_uuid: UUID | None = None,
    status: str = "all",
    min_school_count: int = 0,
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[dict[str, Any]], int]:
    school_id = None
    if isinstance(actor, User) and actor.type in (
        UserAccountType.EDUCATOR,
        UserAccountType.SCHOOL_ADMIN,
    ):
        school_id = getattr(actor, "school_id", None)
        if school_id is None:
            raise WorkspaceForbidden("School membership required")
    elif isinstance(actor, User) and actor.type != UserAccountType.WRIVETED:
        raise WorkspaceForbidden("Review queue access requires staff permission")
    elif school_uuid is not None:
        school = await organisation_repository.get_library(session, school_uuid)
        if school is None:
            raise WorkspaceNotFound("School not found")
        school_id = school.id
    return await review_repository.get_review_queue(
        db=session,
        school_id=school_id,
        status=status,
        min_school_count=min_school_count,
        skip=skip,
        limit=limit,
    )


async def get_library_review_queue(
    session: AsyncSession,
    actor: User,
    *,
    library_uuid: UUID,
    status: str = "all",
    min_school_count: int = 0,
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[dict[str, Any]], int]:
    access = await resolve_library(session, actor, library_uuid, "review")
    return await review_repository.get_review_queue(
        db=session,
        school_id=access.library.id,
        status=status,
        min_school_count=min_school_count,
        skip=skip,
        limit=limit,
    )

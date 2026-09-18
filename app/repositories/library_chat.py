from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cms import FlowDefinition
from app.models.library_chat import LibraryChatSettings
from app.schemas.library_chat import LibraryChatPolicy


async def get_settings(
    session: AsyncSession, library_uuid: UUID
) -> LibraryChatSettings | None:
    return await session.get(LibraryChatSettings, library_uuid, populate_existing=True)


async def default_flow_id(session: AsyncSession) -> UUID | None:
    return await session.scalar(
        select(FlowDefinition.id)
        .where(
            FlowDefinition.info["seed_key"].astext == "huey-bookbot",
            FlowDefinition.is_active.is_(True),
            FlowDefinition.is_published.is_(True),
        )
        .order_by(FlowDefinition.updated_at.desc(), FlowDefinition.id)
        .limit(1)
    )


def save_settings(
    session: AsyncSession,
    library_uuid: UUID,
    stored: LibraryChatSettings | None,
    policy: LibraryChatPolicy,
    revision: int,
) -> LibraryChatSettings:
    if stored is None:
        stored = LibraryChatSettings(library_uuid=library_uuid)
        session.add(stored)
    for field in LibraryChatPolicy.model_fields:
        setattr(stored, field, getattr(policy, field))
    stored.revision = revision
    stored.updated_at = func.now()
    return stored

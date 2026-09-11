"""Operational aggregates for the delivery queue."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.models.event_outbox import EventOutbox, EventStatus


def slack_destination() -> ColumnElement[bool]:
    return or_(
        EventOutbox.destination == "slack", EventOutbox.destination.startswith("slack:")
    )


@dataclass(frozen=True)
class OutboxHealth:
    pending_count: int
    disabled_count: int
    oldest_pending_age_seconds: float


async def get_outbox_health(db: AsyncSession, *, slack_enabled: bool) -> OutboxHealth:
    enabled = ~slack_destination() if not slack_enabled else True
    result = await db.execute(
        select(
            func.count().filter(enabled),
            func.count(),
            func.min(case((enabled, EventOutbox.created_at))),
        ).where(
            EventOutbox.status.in_(
                [EventStatus.PENDING, EventStatus.FAILED, EventStatus.PROCESSING]
            )
        )
    )
    pending_count, total_count, oldest = result.one()
    age = max(0.0, (datetime.utcnow() - oldest).total_seconds()) if oldest else 0.0
    return OutboxHealth(pending_count, total_count - pending_count, round(age, 1))

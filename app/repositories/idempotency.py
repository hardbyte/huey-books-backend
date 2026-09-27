from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.idempotency import IdempotencyRecord

# Advisory lock namespace. hashtextextended takes a seed, so operations that
# lock on unrelated strings (see app/repositories/people.py) cannot collide
# with request keys.
_LOCK_SEED = 0x1D3A


async def lock(db: AsyncSession, operation: str, key: UUID) -> None:
    await db.execute(
        select(
            func.pg_advisory_xact_lock(
                func.hashtextextended(f"{operation}:{key}", _LOCK_SEED)
            )
        )
    )


async def get(db: AsyncSession, operation: str, key: UUID) -> IdempotencyRecord | None:
    return await db.get(IdempotencyRecord, (operation, key))


def save(
    db: AsyncSession,
    operation: str,
    key: UUID,
    actor_id: UUID,
    fingerprint: str,
    response: dict[str, Any],
) -> IdempotencyRecord:
    record = IdempotencyRecord(
        operation=operation,
        key=key,
        actor_id=actor_id,
        fingerprint=fingerprint,
        response=response,
    )
    db.add(record)
    return record

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class IdempotencyRecord(Base):
    """A replayable result for a client-supplied request key.

    Write operations that a client may retry after a lost response record their
    response here inside the same transaction as their effects, so a retry
    returns the original identifiers instead of attempting the work twice.
    The key is scoped by operation so unrelated endpoints cannot collide on the
    same client key, and bound to actor and payload fingerprint so a reused key
    with different input is rejected rather than silently answered.
    """

    __tablename__ = "idempotency_records"
    __table_args__ = (Index("ix_idempotency_records_created_at", "created_at"),)

    operation: Mapped[str] = mapped_column(String(64), primary_key=True)
    key: Mapped[UUID] = mapped_column(primary_key=True)
    actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    fingerprint: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

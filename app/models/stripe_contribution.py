import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class StripeContributionReceipt(Base):
    """Idempotency + audit record for a processed one-off school contribution.

    The Stripe checkout session id is the primary key; the webhook claims it with
    an INSERT ... ON CONFLICT DO NOTHING before doing any work, so concurrent
    webhook redeliveries cannot double-activate a school or double-send emails
    (the money is separately protected by a Stripe idempotency key).
    """

    __tablename__ = "stripe_contribution_receipts"  # type: ignore[assignment]

    checkout_session_id: Mapped[str] = mapped_column(String, primary_key=True)

    school_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    amount_total: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # One of: balance_credit, school_activated, grant_extended, credit_failed.
    crediting: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<StripeContributionReceipt {self.checkout_session_id} ({self.crediting})>"
        )

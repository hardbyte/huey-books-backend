import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.schemas import CaseInsensitiveStringEnum

if TYPE_CHECKING:
    from app.models.school import School


class SchoolBillingMethod(CaseInsensitiveStringEnum):
    CARD = "card"
    INVOICE = "invoice"


class SchoolBillingAttemptStatus(CaseInsensitiveStringEnum):
    CREATING = "creating"
    CHECKOUT_OPEN = "checkout_open"
    INVOICE_OPEN = "invoice_open"
    PAID = "paid"
    EXPIRED = "expired"
    FAILED = "failed"
    VOIDED = "voided"
    UNCOLLECTIBLE = "uncollectible"
    CANCELLED = "cancelled"


OPEN_COLLECTIBLE_ATTEMPT_STATUSES = (
    SchoolBillingAttemptStatus.CREATING,
    SchoolBillingAttemptStatus.CHECKOUT_OPEN,
    SchoolBillingAttemptStatus.INVOICE_OPEN,
)


class SchoolBillingAttempt(Base):
    __tablename__ = "school_billing_attempts"  # type: ignore[assignment]

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "schools.wriveted_identifier",
            name="fk_school_billing_attempt_school",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    school: Mapped["School"] = relationship("School")
    method: Mapped[SchoolBillingMethod] = mapped_column(
        Enum(SchoolBillingMethod, name="enum_school_billing_method"), nullable=False
    )
    status: Mapped[SchoolBillingAttemptStatus] = mapped_column(
        Enum(SchoolBillingAttemptStatus, name="enum_school_billing_attempt_status"),
        nullable=False,
        default=SchoolBillingAttemptStatus.CREATING,
    )
    client_idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    configured_price_id: Mapped[str] = mapped_column(String, nullable=False)
    stripe_customer_id: Mapped[str | None] = mapped_column(String, nullable=True)
    stripe_checkout_session_id: Mapped[str | None] = mapped_column(
        String, nullable=True, unique=True
    )
    stripe_subscription_id: Mapped[str | None] = mapped_column(
        String, nullable=True, index=True
    )
    stripe_invoice_id: Mapped[str | None] = mapped_column(
        String, nullable=True, index=True
    )
    checkout_url: Mapped[str | None] = mapped_column(String, nullable=True)
    hosted_invoice_url: Mapped[str | None] = mapped_column(String, nullable=True)
    billing_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    billing_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    purchase_order_number: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    invoice_days_until_due: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_stripe_event_created_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    failure_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "school_id",
            "client_idempotency_key",
            name="uq_school_billing_attempt_school_idempotency_key",
        ),
        Index(
            "uq_school_billing_attempt_one_open_collectible",
            "school_id",
            unique=True,
            postgresql_where=text(
                "status IN ('CREATING', 'CHECKOUT_OPEN', 'INVOICE_OPEN')"
            ),
        ),
        Index(
            "ix_school_billing_attempts_school_created_at",
            "school_id",
            text("created_at DESC"),
        ),
        Index(
            "ix_school_billing_attempts_open_expiry",
            "expires_at",
            "school_id",
            postgresql_where=text(
                "status IN ('CREATING', 'CHECKOUT_OPEN', 'INVOICE_OPEN')"
            ),
        ),
    )


class SchoolBillingAccount(Base):
    __tablename__ = "school_billing_accounts"  # type: ignore[assignment]

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "schools.wriveted_identifier",
            name="fk_school_billing_account_school",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )
    school: Mapped["School"] = relationship("School")
    stripe_customer_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class StripeEventReceipt(Base):
    __tablename__ = "stripe_event_receipts"  # type: ignore[assignment]

    event_id: Mapped[str] = mapped_column(String, primary_key=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    event_created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    api_version: Mapped[str | None] = mapped_column(String, nullable=True)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

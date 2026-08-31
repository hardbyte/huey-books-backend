import uuid
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from fastapi_permissions import All, Allow  # type: ignore[import-untyped]
from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.schemas import CaseInsensitiveStringEnum

if TYPE_CHECKING:
    from app.models.parent import Parent
    from app.models.product import Product
    from app.models.school import School


class SubscriptionProvider(CaseInsensitiveStringEnum):
    STRIPE = "stripe"


class SubscriptionType(CaseInsensitiveStringEnum):
    FAMILY = "family"
    LIBRARY = "library"
    SCHOOL = "school"


class Subscription(Base):
    __tablename__ = "subscriptions"  # type: ignore[assignment]

    id: Mapped[str] = mapped_column(String, primary_key=True)

    parent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "parents.id", name="fk_parent_stripe_subscription", ondelete="CASCADE"
        ),
        nullable=True,
        index=True,
    )
    parent: Mapped[Optional["Parent"]] = relationship(
        "Parent", back_populates="subscription"
    )

    school_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "schools.wriveted_identifier",
            name="fk_school_stripe_subscription",
            ondelete="CASCADE",
        ),
        nullable=True,
        index=True,
    )
    school: Mapped[Optional["School"]] = relationship(
        "School", back_populates="subscription"
    )

    type: Mapped[SubscriptionType] = mapped_column(
        Enum(SubscriptionType, name="enum_subscription_type"),
        nullable=False,
        default=SubscriptionType.FAMILY,
    )
    stripe_customer_id: Mapped[str] = mapped_column(String, nullable=False, index=True)

    # Note a subscription may be inactive (e.g. the user has cancelled)
    # but still have an expiration date in the future.
    is_active: Mapped[bool] = mapped_column(Boolean(), default=False)
    expiration: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.utcnow() + timedelta(days=30), nullable=False
    )
    product_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("products.id", name="fk_product_stripe_subscription"),
        nullable=False,
        index=True,
    )
    product: Mapped["Product"] = relationship("Product", back_populates="subscriptions")

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    info: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        MutableDict.as_mutable(JSONB)
    )  # type: ignore[arg-type]
    provider: Mapped[SubscriptionProvider] = mapped_column(
        Enum(SubscriptionProvider, name="enum_subscription_provider"),
        nullable=False,
        default=SubscriptionProvider.STRIPE,
    )

    latest_checkout_session_id: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, index=True
    )
    stripe_status: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    collection_method: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_stripe_event_created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )

    __table_args__ = (
        Index(
            "ix_subscriptions_active_expiration",
            "expiration",
            "school_id",
            postgresql_where=text("is_active"),
        ),
    )

    def __acl__(self) -> List[tuple[Any, str, Any]]:
        res = [
            (Allow, "role:admin", All),
        ]

        if self.parent_id is not None:
            res.append((Allow, f"user:{self.parent_id}", "read"))

        if self.school_id is not None:
            res.append((Allow, f"school:{self.school_id}", "read"))

        return res

    def __repr__(self) -> str:
        return f"<Subscription {self.id} - {self.type} - {self.product_id}>"

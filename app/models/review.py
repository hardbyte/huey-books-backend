import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.schemas import CaseInsensitiveStringEnum

if TYPE_CHECKING:
    from app.models.user import User


class ReviewableType(CaseInsensitiveStringEnum):
    LABELSET = "LABELSET"
    CMS_CONTENT = "CMS_CONTENT"
    FLOW_DEFINITION = "FLOW_DEFINITION"


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    reviewable_type: Mapped[ReviewableType] = mapped_column(nullable=False)
    reviewable_id: Mapped[str] = mapped_column(String, nullable=False)

    reviewer_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_review_user", ondelete="CASCADE"),
        nullable=False,
    )
    reviewer: Mapped["User"] = relationship("User", foreign_keys=[reviewer_user_id])

    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    assessment: Mapped[Optional[dict]] = mapped_column(
        MutableDict.as_mutable(JSONB), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.current_timestamp(),
        default=datetime.utcnow,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.current_timestamp(),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "reviewable_type",
            "reviewable_id",
            "reviewer_user_id",
            name="uq_reviews_type_entity_reviewer",
        ),
        Index("ix_reviews_type_entity", "reviewable_type", "reviewable_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<Review id={self.id} type={self.reviewable_type} "
            f"entity={self.reviewable_id} reviewer={self.reviewer_user_id}>"
        )

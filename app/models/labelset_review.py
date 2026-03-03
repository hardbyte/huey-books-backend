import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.labelset import RecommendStatus

if TYPE_CHECKING:
    from app.models.labelset import LabelSet
    from app.models.user import User


class LabelSetReview(Base):
    __tablename__ = "labelset_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    labelset_id: Mapped[int] = mapped_column(
        ForeignKey(
            "labelsets.id", name="fk_labelset_review_labelset", ondelete="CASCADE"
        ),
        nullable=False,
        index=True,
    )
    labelset: Mapped["LabelSet"] = relationship("LabelSet", back_populates="reviews")

    reviewer_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_labelset_review_user", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    reviewer: Mapped["User"] = relationship("User")

    hue_primary_key: Mapped[Optional[str]] = mapped_column(
        ForeignKey("hues.key", name="fk_labelset_review_hue_primary"),
        nullable=True,
    )
    hue_secondary_key: Mapped[Optional[str]] = mapped_column(
        ForeignKey("hues.key", name="fk_labelset_review_hue_secondary"),
        nullable=True,
    )
    hue_tertiary_key: Mapped[Optional[str]] = mapped_column(
        ForeignKey("hues.key", name="fk_labelset_review_hue_tertiary"),
        nullable=True,
    )

    min_age: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_age: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    reading_ability_key: Mapped[Optional[str]] = mapped_column(nullable=True)

    recommend_status: Mapped[Optional[RecommendStatus]] = mapped_column(
        Enum(RecommendStatus), nullable=True
    )

    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    confirmed_existing: Mapped[Optional[bool]] = mapped_column(nullable=True)

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
            "labelset_id",
            "reviewer_user_id",
            name="uq_labelset_reviews_labelset_reviewer",
        ),
    )

    def __repr__(self) -> str:
        return f"<LabelSetReview id={self.id} labelset={self.labelset_id} reviewer={self.reviewer_user_id}>"

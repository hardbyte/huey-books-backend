from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class LibraryChatSettings(Base):
    __tablename__ = "library_chat_settings"
    __table_args__ = (
        CheckConstraint(
            "catalogue_policy IN ('library_only', 'prefer_library')",
            name="valid_catalogue_policy",
        ),
        CheckConstraint("revision > 0", name="positive_revision"),
    )

    library_uuid: Mapped[UUID] = mapped_column(
        ForeignKey("schools.wriveted_identifier", ondelete="CASCADE"), primary_key=True
    )
    catalogue_policy: Mapped[str] = mapped_column(String(32), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    jokes_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    spelling_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

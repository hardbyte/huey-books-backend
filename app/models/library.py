from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Library(Base):
    __tablename__ = "libraries"
    __table_args__ = (UniqueConstraint("organisation_id", "id"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.uuidv7())
    organisation_id: Mapped[UUID] = mapped_column(
        ForeignKey("organisations.id", ondelete="RESTRICT"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class EducationUnit(Base):
    __tablename__ = "education_units"
    __table_args__ = (UniqueConstraint("organisation_id", "id"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.uuidv7())
    organisation_id: Mapped[UUID] = mapped_column(
        ForeignKey("organisations.id", ondelete="RESTRICT"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class EducationUnitLibrary(Base):
    __tablename__ = "education_unit_libraries"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "education_unit_id"],
            ["education_units.organisation_id", "education_units.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organisation_id", "library_id"],
            ["libraries.organisation_id", "libraries.id"],
            ondelete="RESTRICT",
        ),
    )

    education_unit_id: Mapped[UUID] = mapped_column(primary_key=True)
    library_id: Mapped[UUID] = mapped_column(primary_key=True, index=True)
    organisation_id: Mapped[UUID] = mapped_column(index=True)

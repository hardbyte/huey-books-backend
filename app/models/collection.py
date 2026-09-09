import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from fastapi_permissions import All, Allow  # type: ignore[import-untyped]
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    func,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, column_property, mapped_column, relationship

from app.db import Base
from app.models.collection_item import CollectionItem

if TYPE_CHECKING:
    from app.models.school import School
    from app.models.user import User


class Collection(Base):
    __tablename__ = "collections"  # type: ignore[assignment]
    __table_args__ = (
        Index(
            "uq_collections_school_default",
            "school_id",
            unique=True,
            postgresql_where=text("is_default AND school_id IS NOT NULL"),
        ),
        CheckConstraint(
            "NOT is_default OR school_id IS NOT NULL",
            name="ck_collections_default_school",
        ),
    )

    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
        unique=True,
        primary_key=True,
        index=True,
        nullable=False,
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)

    items: Mapped[List["CollectionItem"]] = relationship(
        "CollectionItem",
        back_populates="collection",
        lazy="select",
        cascade="all, delete-orphan",
    )

    book_count: Mapped[int] = column_property(
        select(func.count(CollectionItem.id))
        .where(CollectionItem.collection_id == id)
        .correlate_except(CollectionItem)
        .scalar_subquery()
    )

    school_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey(
            "schools.wriveted_identifier",
            name="fk_school_collection",
            ondelete="CASCADE",
        ),
        nullable=True,
        index=True,
    )
    school: Mapped[Optional["School"]] = relationship(
        "School", back_populates="collections"
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", name="fk_user_collection", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    user: Mapped[Optional["User"]] = relationship("User", back_populates="collection")

    info: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        MutableDict.as_mutable(JSONB)
    )  # type: ignore[arg-type]
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.current_timestamp(),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    def __repr__(self) -> str:
        def association_string() -> str:
            output = ""
            if self.school:
                output += f"school={self.school} "
            if self.user:
                output += f"user={self.user} "
            return output.strip()

        return f"<Collection '{self.name}' {association_string()} count={self.book_count} id={self.id}>"

    def __acl__(self) -> List[tuple[Any, str, Any]]:
        """
        Defines who can do what to the Collection instance.
        """

        policies = [
            (Allow, "role:admin", All),
            (Allow, "role:lms", All),
        ]

        if self.school:
            policies.append((Allow, f"school:{self.school.id}", "read"))
            policies.append((Allow, f"schooladmin:{self.school.id}", All))

        if self.user:
            policies.append((Allow, f"user:{self.user_id}", All))
            policies.append((Allow, f"parent:{self.user_id}", All))

        return policies

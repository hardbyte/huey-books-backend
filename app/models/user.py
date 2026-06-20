import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from fastapi_permissions import All, Allow  # type: ignore[import-untyped]
from sqlalchemy import Boolean, DateTime, Enum, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.supporter_reader_association import SupporterReaderAssociation
from app.schemas import CaseInsensitiveStringEnum

if TYPE_CHECKING:
    from app.models.booklist import BookList
    from app.models.collection import Collection
    from app.models.event import Event


class UserAccountType(CaseInsensitiveStringEnum):
    WRIVETED = "wriveted"
    STUDENT = "student"
    PUBLIC = "public"
    EDUCATOR = "educator"
    SCHOOL_ADMIN = "school_admin"
    PARENT = "parent"
    SUPPORTER = "supporter"


class User(Base):
    """
    An abstract user.

    https://docs.sqlalchemy.org/en/14/orm/inheritance.html#joined-table-inheritance
    Note: only functionally abstract (has db tables for ORM purposes, but no meaningful instantiation).
    """

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        default=uuid.uuid4,
        unique=True,
        primary_key=True,
        index=True,
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(Boolean(), default=True)

    type: Mapped[UserAccountType] = mapped_column(
        Enum(UserAccountType, name="enum_user_account_type"),
        nullable=False,
        index=True,
        default=UserAccountType.PUBLIC,
    )

    __mapper_args__ = {
        "polymorphic_on": type,
    }

    email: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=True)

    @hybrid_property
    def phone(self) -> Optional[str]:
        return self.info.get("phone") if self.info else None

    # overall "name" string, most likely provided by SSO
    name: Mapped[str] = mapped_column(String, nullable=False)

    info: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        MutableDict.as_mutable(JSONB),
        nullable=True,
        default={},  # type: ignore[arg-type]
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    last_login_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    collection: Mapped[Optional["Collection"]] = relationship(
        "Collection", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )

    booklists: Mapped[List["BookList"]] = relationship(
        "BookList",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    events: Mapped[List["Event"]] = relationship(
        "Event",
        lazy="dynamic",
        back_populates="user",
        cascade="all, delete-orphan",
        order_by="desc(Event.timestamp)",
    )

    newsletter: Mapped[bool] = mapped_column(
        Boolean(), nullable=False, server_default="false"
    )

    # Opt-out from staff broadcasts/announcements (the educator email channel).
    # Existing educators are reachable for service updates by default; this is
    # set true when a recipient clicks the unsubscribe link in a broadcast.
    marketing_opt_out: Mapped[bool] = mapped_column(
        Boolean(), nullable=False, server_default="false"
    )

    # targeting the association instead of the users directly to
    # include the "active" status in any outputs
    supportee_associations: Mapped[list[SupporterReaderAssociation]] = relationship(
        SupporterReaderAssociation,
        back_populates="supporter",
        lazy="dynamic",
    )

    async def get_principals(self) -> List[str]:
        principals = [f"user:{self.id}"]

        # for association in await self.awaitable_attrs.supportee_associations:
        #     if association.is_active:
        #         principals.append(f"supporter:{association.reader_id}")

        return principals

    def __acl__(self) -> List[tuple[Any, str, Any]]:
        """defines who can do what to the instance
        the function returns a list containing tuples in the form of
        (Allow or Deny, principal identifier, permission name)
        If a role is not listed (like "role:user") the access will be
        automatically denied.
        (Deny, Everyone, All) is automatically appended at the end.
        """
        acl = [
            (Allow, f"user:{self.id}", All),
            (Allow, "role:admin", All),
            (Allow, f"supportee:{self.id}", "notify"),
        ]

        return acl

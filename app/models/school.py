import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from fastapi_permissions import All, Allow, Deny  # type: ignore[import-untyped]
from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.associationproxy import association_proxy
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.service_account_school_association import (
    service_account_school_association_table,
)
from app.schemas import CaseInsensitiveStringEnum

if TYPE_CHECKING:
    from app.models.booklist import BookList
    from app.models.class_group import ClassGroup
    from app.models.collection import Collection
    from app.models.country import Country
    from app.models.event import Event
    from app.models.school_admin import SchoolAdmin
    from app.models.service_account import ServiceAccount
    from app.models.subscription import Subscription


# which type of bookbot the school is currently using
class SchoolBookbotType(CaseInsensitiveStringEnum):
    SCHOOL_BOOKS = "school_books"
    HUEY_BOOKS = "huey_books"


class SchoolState(CaseInsensitiveStringEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    # Has initiated onboarding, a user has bound themselves to the school, but onboarding isn't yet completed.
    # Useful for email prompts to remind dropoff users to upload their collections, and other KPI metrics.
    PENDING = "pending"


class School(Base):
    __tablename__ = "schools"  # type: ignore[assignment]

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    country_code: Mapped[Optional[str]] = mapped_column(
        String(3), ForeignKey("countries.id", name="fk_school_country"), index=True
    )
    official_identifier: Mapped[Optional[str]] = mapped_column(String(512))

    # Used for public links to school pages e.g. chatbot
    wriveted_identifier: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
        index=True,
        unique=True,
        nullable=False,
    )

    __table_args__ = (
        # Composite INDEX combining country code and country specific IDs e.g. (AUS, ACARA ID)
        Index(
            "ix_schools_country_code_official_identifier",
            country_code,
            official_identifier,
            unique=True,
        ),
        # Index combining country code and optional state stored in the location key of info.
        # Note alembic can't automatically deal with this, but the migration (and index) exists!
        # Index("index_schools_by_country_state", country_code, postgresql_where=text("(info->'location'->>'state')"))
    )

    state: Mapped[SchoolState] = mapped_column(
        Enum(SchoolState), nullable=False, default=SchoolState.INACTIVE
    )

    name: Mapped[str] = mapped_column(String(256), nullable=False)

    # e.g. "canterbury.ac.nz" if all student email addresses have the form
    # brian.thorne@canterbury.ac.nz - allows automatic registration
    student_domain: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)

    # All users with this email domain will be granted teacher rights
    teacher_domain: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)

    class_groups: Mapped[List["ClassGroup"]] = relationship(
        "ClassGroup", cascade="all, delete-orphan"
    )

    # Extra info:
    # school website
    # Suburb,State,Postcode,
    # Type,Sector,Status,Geolocation,
    # Parent School ID,AGE ID,
    # Latitude,Longitude
    info: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        MutableDict.as_mutable(JSONB)
    )  # type: ignore[arg-type]

    country: Mapped[Optional["Country"]] = relationship("Country")

    collection: Mapped[Optional["Collection"]] = relationship(
        "Collection",
        back_populates="school",
        uselist=False,
        cascade="all, delete-orphan",
    )

    # https://docs.sqlalchemy.org/en/14/orm/extensions/associationproxy.html#simplifying-association-objects
    # association proxy of "collectionitems" collection
    # to "editions" attribute
    editions = association_proxy("collection", "edition")
    works = association_proxy("editions", "work")

    bookbot_type: Mapped[SchoolBookbotType] = mapped_column(
        Enum(SchoolBookbotType),
        nullable=False,
        server_default=SchoolBookbotType.HUEY_BOOKS,
    )

    lms_type: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default="none"
    )

    # students  = list[Student]  (backref)
    # educators = list[Educator] (backref)
    admins: Mapped[List["SchoolAdmin"]] = relationship(
        "SchoolAdmin", overlaps="educators,school"
    )

    booklists: Mapped[List["BookList"]] = relationship(
        "BookList", back_populates="school", cascade="all, delete-orphan"
    )

    events: Mapped[List["Event"]] = relationship(
        "Event", back_populates="school", lazy="dynamic"
    )

    service_accounts: Mapped[List["ServiceAccount"]] = relationship(
        "ServiceAccount",
        secondary=service_account_school_association_table,
        back_populates="schools",
    )

    # A school can transiently have more than one subscription row (e.g. a comped
    # contribution grant that is retired when the school converts to a paying
    # Stripe subscription). uselist=False must therefore resolve deterministically
    # to the "live" one: active first, then latest expiry.
    subscription: Mapped[Optional["Subscription"]] = relationship(
        "Subscription",
        back_populates="school",
        uselist=False,
        cascade="all, delete-orphan",
        order_by="(Subscription.is_active.desc(), Subscription.expiration.desc())",
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

    def __repr__(self) -> str:
        return f"<School '{self.name}' ({self.official_identifier})>"

    def __acl__(self) -> List[tuple[Any, str, Any]]:
        """defines who can do what to the instance
        the function returns a list containing tuples in the form of
        (Allow or Deny, principal identifier, permission name)

        If a role is not listed (like "role:user") the access will be
        automatically denied.

        (Deny, Everyone, All) is automatically appended at the end.
        """
        return [
            (Allow, "role:admin", All),
            (Allow, f"schooladmin:{self.id}", All),
            (Allow, "role:lms", "batch"),
            (Allow, "role:lms", "update"),
            (Allow, "role:lms", "read"),
            (Allow, "role:lms", "read-collection"),
            (Deny, "role:student", "update"),
            (Deny, "role:student", "delete"),
            (Allow, f"school:{self.id}", "read"),
            (Allow, f"school:{self.id}", "read-collection"),
            (Allow, f"school:{self.id}", "update"),
        ]

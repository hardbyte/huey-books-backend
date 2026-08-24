import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, Optional

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.schemas import CaseInsensitiveStringEnum

if TYPE_CHECKING:
    from app.models.school import School
    from app.models.user import User


class SchoolInvitationStatus(CaseInsensitiveStringEnum):
    # Sent, awaiting the invited school's admin to accept.
    SENT = "sent"
    # Accepted: invited school activated and the free-access grant issued.
    ACCEPTED = "accepted"
    # Link expired unredeemed.
    EXPIRED = "expired"
    # Withdrawn by the inviter (or staff) before acceptance.
    REVOKED = "revoked"


class SchoolInvitation(Base):
    """A referral from one (paying) school inviting another to a free trial.

    The grant is only issued when the invited school's admin accepts (see
    ``app.services.school_invitations.accept_invitation``); creating the row just
    records and emails the invitation.
    """

    __tablename__ = "school_invitations"  # type: ignore[assignment]

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        default=uuid.uuid4,
    )
    # Opaque bearer token embedded in the invite email link.
    token: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)

    # Null for a staff (Wriveted) invitation issued on behalf of no particular
    # school — staff can invite any school directly.
    inviter_school_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("schools.wriveted_identifier", name="fk_invitation_inviter_school"),
        index=True,
        nullable=True,
    )
    inviter_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_invitation_inviter_user", ondelete="SET NULL"),
        nullable=True,
    )

    # Set when a reference school was chosen; otherwise the school is created from
    # name+country when the invite is accepted.
    invited_school_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("schools.wriveted_identifier", name="fk_invitation_invited_school"),
        index=True,
        nullable=True,
    )
    invited_school_name: Mapped[str] = mapped_column(String(300), nullable=False)
    country_code: Mapped[Optional[str]] = mapped_column(
        String(3), ForeignKey("countries.id", name="fk_invitation_country")
    )
    invited_contact_email: Mapped[str] = mapped_column(
        String, index=True, nullable=False
    )
    invited_contact_name: Mapped[Optional[str]] = mapped_column(String(200))

    # Optional personal note from the inviter, shown in the invitation email.
    message: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)

    # Days of free access to grant on acceptance (snapshot of the setting/override).
    grant_days: Mapped[int] = mapped_column(Integer, nullable=False)

    status: Mapped[SchoolInvitationStatus] = mapped_column(
        Enum(SchoolInvitationStatus, name="enum_school_invitation_status"),
        nullable=False,
        default=SchoolInvitationStatus.SENT,
        index=True,
    )

    # The comp subscription created when accepted (for audit).
    redeemed_subscription_id: Mapped[Optional[str]] = mapped_column(String)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.current_timestamp(),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
    accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    info: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB)  # type: ignore[arg-type]

    inviter_school: Mapped[Optional["School"]] = relationship(
        "School", foreign_keys=[inviter_school_id]
    )
    invited_school: Mapped[Optional["School"]] = relationship(
        "School", foreign_keys=[invited_school_id]
    )
    inviter_user: Mapped[Optional["User"]] = relationship("User")

    def __repr__(self) -> str:
        return (
            f"<SchoolInvitation {self.id} {self.status} "
            f"inviter={self.inviter_school_id} invited={self.invited_school_name!r}>"
        )

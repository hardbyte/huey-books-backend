from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from app.models.school_invitation import SchoolInvitationStatus


class SchoolInvitationCreate(BaseModel):
    """Send an invite to a peer school. Either reference an existing (inactive)
    school by id, or supply a new school's name + country."""

    invited_school_wriveted_id: Optional[UUID] = None
    invited_school_name: Optional[str] = Field(None, max_length=300)
    country_code: Optional[str] = Field(None, min_length=3, max_length=3)
    contact_email: EmailStr
    contact_name: Optional[str] = Field(None, max_length=200)
    # Optional personal note included in the invitation email.
    message: Optional[str] = Field(None, max_length=2000)
    # Optional per-invite override; defaults to INVITE_GRANT_DAYS.
    grant_days: Optional[int] = Field(None, ge=1, le=3650)

    @model_validator(mode="after")
    def _require_target(self):
        if self.invited_school_wriveted_id is None and not (
            self.invited_school_name and self.country_code
        ):
            raise ValueError(
                "Provide invited_school_wriveted_id, or both invited_school_name and country_code."
            )
        return self


class SchoolInvitationDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    invited_school_name: str
    invited_contact_email: str
    invited_contact_name: Optional[str] = None
    message: Optional[str] = None
    status: SchoolInvitationStatus
    grant_days: int
    created_at: datetime
    expires_at: datetime
    accepted_at: Optional[datetime] = None


class SchoolInvitationAllowance(BaseModel):
    """A school's invite allowance for the current window."""

    base: int
    staff_bonus: int
    earned_bonus: int
    total: int
    used: int
    remaining: int
    window_days: int


class GrantBonusInvites(BaseModel):
    additional: int = Field(..., ge=1, le=100)


class SchoolInvitationPreview(BaseModel):
    """Minimal, token-addressed view for the accept page."""

    inviter_school_name: str
    invited_school_name: str
    grant_days: int
    status: SchoolInvitationStatus
    expired: bool


class SchoolInvitationAcceptResponse(BaseModel):
    school_wriveted_id: UUID
    school_name: str
    access_until: Optional[datetime] = None
    message: str

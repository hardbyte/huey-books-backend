from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.user import UserAccountType


class BroadcastAudience(BaseModel):
    """A user segment to email.

    Filters are ANDed. An empty/omitted ``user_types`` means all account types.
    ``country_code`` and ``school_id`` only match users affiliated with a school
    (educators, school admins, students); users with no school (e.g. parents,
    public) are excluded when those filters are set.
    """

    # Account types to include, e.g. ["educator", "school_admin"], ["parent"].
    # Required and non-empty: an empty segment would email everyone, which must
    # be an explicit choice (select every type) rather than an accidental default.
    user_types: list[UserAccountType] = Field(min_length=1)
    # 3-letter country code as stored on schools (e.g. "NZL", "AUS").
    country_code: Optional[str] = None
    # A school's wriveted_identifier.
    school_id: Optional[UUID] = None


class BroadcastPreview(BaseModel):
    recipient_count: int
    sample_names: list[str] = []


class BroadcastSendIn(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    # Plain text (blank lines separate paragraphs); rendered to safe HTML.
    body: str = Field(min_length=1)
    audience: BroadcastAudience = Field(default_factory=BroadcastAudience)


class BroadcastTestIn(BaseModel):
    """Send the composed email only to the requesting staff member."""

    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1)


class BroadcastSendResult(BaseModel):
    queued: int

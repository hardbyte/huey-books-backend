from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class BroadcastAudience(BaseModel):
    """Who a broadcast goes to. ``school`` requires ``school_id``."""

    scope: Literal["all_educators", "school"] = "all_educators"
    # A school's wriveted_identifier; required when scope == "school".
    school_id: Optional[UUID] = None


class BroadcastPreview(BaseModel):
    recipient_count: int
    sample_names: list[str] = []


class BroadcastSendIn(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    # Plain text (blank lines separate paragraphs); rendered to safe HTML.
    body: str = Field(min_length=1)
    audience: BroadcastAudience = BroadcastAudience()


class BroadcastSendResult(BaseModel):
    queued: int

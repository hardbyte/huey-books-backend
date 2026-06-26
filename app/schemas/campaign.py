import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.models.campaign import CampaignBiasMode
from app.models.cms import ContentVisibility


class CampaignTargeting(BaseModel):
    country_codes: Optional[list[str]] = None
    region_states: Optional[list[str]] = None
    school_ids: Optional[list[int]] = None
    min_age: Optional[int] = None
    max_age: Optional[int] = None
    targeting_cel: Optional[str] = None


class CampaignBase(CampaignTargeting):
    name: str
    description: Optional[str] = None
    slug: Optional[str] = None
    flow_id: Optional[uuid.UUID] = None
    theme_id: Optional[uuid.UUID] = None
    booklist_id: Optional[uuid.UUID] = None
    bias_mode: CampaignBiasMode = CampaignBiasMode.BOOST
    active_from: Optional[datetime] = None
    active_until: Optional[datetime] = None
    priority: int = 0
    is_active: bool = True
    visibility: ContentVisibility = ContentVisibility.WRIVETED
    school_id: Optional[int] = None


class CampaignCreateIn(CampaignBase):
    pass


class CampaignUpdateIn(BaseModel):
    """All fields optional; only provided fields are patched."""

    name: Optional[str] = None
    description: Optional[str] = None
    slug: Optional[str] = None
    flow_id: Optional[uuid.UUID] = None
    theme_id: Optional[uuid.UUID] = None
    booklist_id: Optional[uuid.UUID] = None
    bias_mode: Optional[CampaignBiasMode] = None
    country_codes: Optional[list[str]] = None
    region_states: Optional[list[str]] = None
    school_ids: Optional[list[int]] = None
    min_age: Optional[int] = None
    max_age: Optional[int] = None
    targeting_cel: Optional[str] = None
    active_from: Optional[datetime] = None
    active_until: Optional[datetime] = None
    priority: Optional[int] = None
    is_active: Optional[bool] = None
    visibility: Optional[ContentVisibility] = None
    school_id: Optional[int] = None


class CampaignBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    visibility: ContentVisibility
    is_active: bool
    priority: int
    flow_id: Optional[uuid.UUID] = None
    theme_id: Optional[uuid.UUID] = None
    booklist_id: Optional[uuid.UUID] = None
    active_from: Optional[datetime] = None
    active_until: Optional[datetime] = None


class CampaignDetail(CampaignBrief):
    description: Optional[str] = None
    slug: Optional[str] = None
    bias_mode: CampaignBiasMode
    country_codes: Optional[list[str]] = None
    region_states: Optional[list[str]] = None
    school_ids: Optional[list[int]] = None
    min_age: Optional[int] = None
    max_age: Optional[int] = None
    targeting_cel: Optional[str] = None
    school_id: Optional[int] = None
    created_by: Optional[uuid.UUID] = None
    created_at: datetime
    updated_at: datetime
    published_at: Optional[datetime] = None


class ResolvedCampaign(BaseModel):
    """What the resolver returns for a session context."""

    campaign_id: uuid.UUID
    name: str
    flow_id: Optional[uuid.UUID] = None
    theme_id: Optional[uuid.UUID] = None
    booklist_id: Optional[uuid.UUID] = None
    bias_mode: CampaignBiasMode

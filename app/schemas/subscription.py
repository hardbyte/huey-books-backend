from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict

from app.models.subscription import SubscriptionProvider, SubscriptionType
from app.schemas.product import ProductBrief


class SubscriptionBrief(BaseModel):
    id: str
    type: SubscriptionType = SubscriptionType.FAMILY
    provider: Literal[SubscriptionProvider.STRIPE]
    is_active: bool
    expiration: datetime | None = None
    product: ProductBrief
    model_config = ConfigDict(from_attributes=True)


class SubscriptionDetail(SubscriptionBrief):
    stripe_customer_id: str
    created_at: datetime
    updated_at: datetime
    info: Optional[Any] = None


class SubscriptionCreateIn(BaseModel):
    id: str
    type: SubscriptionType = SubscriptionType.FAMILY
    stripe_customer_id: str
    parent_id: str | None = None
    school_id: str | None = None
    product_id: str
    is_active: bool | None = None
    info: dict | None = None
    latest_checkout_session_id: str | None = None
    expiration: datetime | None = None
    stripe_status: str | None = None
    collection_method: str | None = None
    paid_at: datetime | None = None
    last_stripe_event_created_at: datetime | None = None


class SubscriptionUpdateIn(BaseModel):
    product_id: str | None = None
    is_active: bool | None = None
    info: dict | None = None
    latest_checkout_session_id: str | None = None
    expiration: datetime | None = None
    stripe_status: str | None = None
    collection_method: str | None = None
    paid_at: datetime | None = None
    last_stripe_event_created_at: datetime | None = None

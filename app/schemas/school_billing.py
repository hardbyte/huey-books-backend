from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.models.school_billing import SchoolBillingAttemptStatus, SchoolBillingMethod


class SchoolBillingEntitlement(BaseModel):
    active: bool
    source: str | None = None
    expires_at: datetime | None = None


class SchoolBillingAttemptBrief(BaseModel):
    id: UUID
    method: SchoolBillingMethod
    status: SchoolBillingAttemptStatus
    checkout_url: str | None = None
    hosted_invoice_url: str | None = None
    billing_email: str | None = None
    billing_name: str | None = None
    purchase_order_number: str | None = None
    expires_at: datetime | None = None
    failure_reason: str | None = None


class PaidSchoolSubscription(BaseModel):
    id: str
    stripe_status: str | None = None
    expires_at: datetime


class SchoolBillingCapabilities(BaseModel):
    card: bool
    invoice: bool
    manage: bool
    blocking_reason: str | None = None


class SchoolBillingOffer(BaseModel):
    price_id: str
    unit_amount: int
    currency: str
    interval: str
    interval_count: int
    invoice_days_until_due: int


class SchoolBillingStatus(BaseModel):
    entitlement: SchoolBillingEntitlement
    current_attempt: SchoolBillingAttemptBrief | None = None
    paid_subscription: PaidSchoolSubscription | None = None
    capabilities: SchoolBillingCapabilities
    invoice_first: bool
    offer: SchoolBillingOffer


class SchoolBillingStartResult(BaseModel):
    attempt_id: UUID
    method: SchoolBillingMethod
    status: SchoolBillingAttemptStatus
    checkout_url: str | None = None
    hosted_invoice_url: str | None = None

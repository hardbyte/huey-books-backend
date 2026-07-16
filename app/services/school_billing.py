"""Stripe Checkout for school subscriptions.

Creates a Checkout Session (subscription mode) for the flat annual school
price. The session is scoped to the school via ``client_reference_id`` (the
webhook resolves schools from it), so the returned URL can be paid by the
school admin or forwarded to a sponsor (parent, library) — the subscription
attaches to the school regardless of who pays.
"""

import asyncio

import stripe
from structlog import get_logger

from app.config import get_settings
from app.models.school import School

logger = get_logger()
settings = get_settings()


class SchoolBillingError(Exception):
    """Raised when a school checkout session cannot be created."""


async def create_school_checkout_session(school: School) -> str:
    """Create a Stripe Checkout Session for the school and return its URL."""
    if not settings.STRIPE_SCHOOL_PRICE_ID:
        raise SchoolBillingError("STRIPE_SCHOOL_PRICE_ID is not configured")
    if not settings.STRIPE_SECRET_KEY:
        raise SchoolBillingError("STRIPE_SECRET_KEY is not configured")

    stripe.api_key = settings.STRIPE_SECRET_KEY
    app_url = settings.HUEY_BOOKS_APP_URL.rstrip("/")
    wriveted_id = str(school.wriveted_identifier)
    onboarding = (school.info or {}).get("onboarding") or {}
    contact_email = onboarding.get("contact_email")

    params = {
        "mode": "subscription",
        "line_items": [{"price": settings.STRIPE_SCHOOL_PRICE_ID, "quantity": 1}],
        "client_reference_id": wriveted_id,
        "success_url": f"{app_url}/school/onboarding/success?session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{app_url}/school/onboarding/cancelled",
        "metadata": {"wriveted_school_id": wriveted_id, "school_name": school.name},
        "allow_promotion_codes": True,
    }
    if contact_email:
        params["customer_email"] = contact_email

    try:
        # The Stripe SDK is synchronous; offload so it doesn't block the loop.
        session = await asyncio.to_thread(stripe.checkout.Session.create, **params)
    except Exception as e:
        logger.error(
            "Failed to create school checkout session",
            wriveted_school_id=wriveted_id,
            error=str(e),
        )
        raise SchoolBillingError(f"Could not create checkout session: {e}")

    logger.info(
        "Created school checkout session",
        wriveted_school_id=wriveted_id,
        checkout_session_id=session.id,
    )
    return session.url

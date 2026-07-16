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


async def create_school_checkout_session(
    school: School, price_id: str | None = None
) -> str:
    """Create a Stripe Checkout Session for the school and return its URL.

    ``price_id`` selects one of the configured school prices; it must be one of
    ``STRIPE_SCHOOL_PRICE_IDS`` (defaults to the first).
    """
    if not settings.STRIPE_SCHOOL_PRICE_IDS:
        raise SchoolBillingError("STRIPE_SCHOOL_PRICE_IDS is not configured")
    if not settings.STRIPE_SECRET_KEY:
        raise SchoolBillingError("STRIPE_SECRET_KEY is not configured")

    if price_id is None:
        price_id = settings.STRIPE_SCHOOL_PRICE_IDS[0]
    elif price_id not in settings.STRIPE_SCHOOL_PRICE_IDS:
        raise SchoolBillingError(f"Unknown school price id: {price_id}")

    stripe.api_key = settings.STRIPE_SECRET_KEY
    app_url = settings.HUEY_BOOKS_APP_URL.rstrip("/")
    wriveted_id = str(school.wriveted_identifier)
    onboarding = (school.info or {}).get("onboarding") or {}
    contact_email = onboarding.get("contact_email")

    params = {
        "mode": "subscription",
        "line_items": [{"price": price_id, "quantity": 1}],
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


async def create_school_contribution_checkout_session(
    school: School, price_id: str | None = None
) -> str:
    """Create a one-off "contribute a month" Checkout Session and return its URL.

    Unlike ``create_school_checkout_session`` this is a one-off payment
    (``mode="payment"``), not a subscription. It is scoped to the school via
    ``client_reference_id`` so the URL can be shared with any supporter (parent,
    public sponsor, library) — the contribution funds that school regardless of
    who pays. ``metadata["kind"]`` marks it as a contribution so the webhook can
    distinguish it from a subscription checkout.

    ``price_id`` selects one of the configured contribution prices; it must be
    one of ``STRIPE_SCHOOL_CONTRIBUTION_PRICE_IDS`` (defaults to the first).
    """
    if not settings.STRIPE_SCHOOL_CONTRIBUTION_PRICE_IDS:
        raise SchoolBillingError(
            "STRIPE_SCHOOL_CONTRIBUTION_PRICE_IDS is not configured"
        )
    if not settings.STRIPE_SECRET_KEY:
        raise SchoolBillingError("STRIPE_SECRET_KEY is not configured")

    if price_id is None:
        price_id = settings.STRIPE_SCHOOL_CONTRIBUTION_PRICE_IDS[0]
    elif price_id not in settings.STRIPE_SCHOOL_CONTRIBUTION_PRICE_IDS:
        raise SchoolBillingError(f"Unknown contribution price id: {price_id}")

    stripe.api_key = settings.STRIPE_SECRET_KEY
    app_url = settings.HUEY_BOOKS_APP_URL.rstrip("/")
    wriveted_id = str(school.wriveted_identifier)

    params = {
        "mode": "payment",
        "line_items": [{"price": price_id, "quantity": 1}],
        "client_reference_id": wriveted_id,
        "success_url": f"{app_url}/school/contribute/success?session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{app_url}/school/contribute/cancelled",
        "metadata": {
            "kind": "school_contribution",
            "wriveted_school_id": wriveted_id,
            "school_name": school.name,
        },
        "allow_promotion_codes": True,
    }

    try:
        # The Stripe SDK is synchronous; offload so it doesn't block the loop.
        session = await asyncio.to_thread(stripe.checkout.Session.create, **params)
    except Exception as e:
        logger.error(
            "Failed to create school contribution checkout session",
            wriveted_school_id=wriveted_id,
            error=str(e),
        )
        raise SchoolBillingError(f"Could not create contribution checkout session: {e}")

    logger.info(
        "Created school contribution checkout session",
        wriveted_school_id=wriveted_id,
        checkout_session_id=session.id,
    )
    return session.url

"""Stripe Checkout for school subscriptions.

Creates a Checkout Session (subscription mode) for the flat annual school
price. The session is scoped to the school via ``client_reference_id`` (the
webhook resolves schools from it), so the returned URL can be paid by the
school admin or forwarded to a sponsor (parent, library) — the subscription
attaches to the school regardless of who pays.
"""

import asyncio
from datetime import datetime, timedelta

import stripe
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from app.config import get_settings
from app.models.school import School
from app.models.subscription import Subscription
from app.services.school_access import (
    get_active_stripe_subscription_async,
    grant_invoice_pending_access,
    known_stripe_customer_id_async,
    lock_school_access_async,
)

logger = get_logger()
settings = get_settings()


class SchoolBillingError(Exception):
    """Raised when a school checkout session cannot be created."""


class SchoolInvoiceConflictError(SchoolBillingError):
    """The school already has a live Stripe subscription (card or invoice)."""


def _select_school_price_id(school: School, price_id: str | None) -> str:
    """Resolve the Stripe price id for a school subscription (checkout or invoice).

    Shared by card checkout and invoice subscriptions so both honour the same
    per-country selection (``STRIPE_SCHOOL_PRICE_IDS_BY_COUNTRY`` keyed by
    ``country_code``, else the default ``STRIPE_SCHOOL_PRICE_IDS[0]``). An
    explicit ``price_id`` must be one of the configured prices.
    """
    if not settings.STRIPE_SCHOOL_PRICE_IDS:
        raise SchoolBillingError("STRIPE_SCHOOL_PRICE_IDS is not configured")

    by_country = settings.STRIPE_SCHOOL_PRICE_IDS_BY_COUNTRY or {}
    allowed_price_ids = set(settings.STRIPE_SCHOOL_PRICE_IDS) | set(by_country.values())
    if price_id is None:
        return (
            by_country.get(school.country_code) or settings.STRIPE_SCHOOL_PRICE_IDS[0]
        )
    if price_id not in allowed_price_ids:
        raise SchoolBillingError(f"Unknown school price id: {price_id}")
    return price_id


async def create_school_checkout_session(
    school: School,
    *,
    session: AsyncSession | None = None,
    price_id: str | None = None,
) -> str:
    """Create a Stripe Checkout Session for the school and return its URL.

    ``price_id`` selects one of the configured school prices. When omitted it
    defaults to the school's country-specific price
    (``STRIPE_SCHOOL_PRICE_IDS_BY_COUNTRY`` keyed by ``country_code``), falling
    back to the first of ``STRIPE_SCHOOL_PRICE_IDS``. An explicit ``price_id``
    must be one of the configured prices (list or per-country map).
    """
    if not settings.STRIPE_SECRET_KEY:
        raise SchoolBillingError("STRIPE_SECRET_KEY is not configured")

    price_id = _select_school_price_id(school, price_id)

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
    # Reuse the school's existing Stripe Customer so a repeat checkout doesn't
    # create a duplicate. Stripe rejects customer + customer_email together, so
    # only fall back to customer_email when no customer is known.
    known_customer_id = (
        await known_stripe_customer_id_async(session, school.wriveted_identifier)
        if session is not None
        else None
    )
    if known_customer_id:
        params["customer"] = known_customer_id
    elif contact_email:
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


async def create_school_billing_portal_session(
    session: AsyncSession, school: School
) -> str | None:
    """Return a Stripe Billing Portal URL for the school, or ``None`` if it has
    no real Stripe subscription to manage.

    The portal lets an admin update the card, download invoices, or cancel.
    Resolves the school's active *paying* subscription (card or invoice) via the
    shared predicate; comped grants (empty ``stripe_customer_id``) are excluded.
    """
    if not settings.STRIPE_SECRET_KEY:
        raise SchoolBillingError("STRIPE_SECRET_KEY is not configured")

    subscription = await get_active_stripe_subscription_async(
        session, school.wriveted_identifier
    )
    if subscription is None or not subscription.stripe_customer_id:
        return None

    stripe.api_key = settings.STRIPE_SECRET_KEY
    app_url = settings.HUEY_BOOKS_APP_URL.rstrip("/")
    return_url = f"{app_url}/school/{school.wriveted_identifier}"

    try:
        portal_session = await asyncio.to_thread(
            stripe.billing_portal.Session.create,
            customer=subscription.stripe_customer_id,
            return_url=return_url,
        )
    except Exception as e:
        logger.error(
            "Failed to create billing portal session",
            wriveted_school_id=str(school.wriveted_identifier),
            error=str(e),
        )
        raise SchoolBillingError(f"Could not create billing portal session: {e}")

    return portal_session.url


async def create_school_invoice_subscription(
    session: AsyncSession,
    school: School,
    *,
    billing_email: str,
    billing_name: str | None = None,
    po_number: str | None = None,
    price_id: str | None = None,
) -> dict:
    """Create a fully-automated net-terms invoice subscription for a school.

    Zero manual staff steps: Stripe finalises + emails the invoice, sends the
    reminder schedule, collects payment, and renews the same way each period.
    The school is granted immediate net-terms access via an ``invoice_pending``
    comp grant (retired on payment, lapsed by the sweep if never paid).

    Depends on these Stripe **Dashboard** settings for the automation to close
    the loop (see the reviewer note in the PR):
    - Billing → Subscriptions and emails: *email finalized invoices* ON and a
      *reminder schedule* configured;
    - Billing → Manage failed payments / unpaid invoices: *cancel the
      subscription when an invoice is N days overdue* — this is the load-bearing
      setting that emits ``customer.subscription.deleted`` for a never-paid
      invoice, which drops the school to INACTIVE once the grant lapses.

    The webhook/account API version should be pinned (invoice→subscription moved
    to ``invoice.parent.subscription_details.subscription`` on 2025+ versions;
    the webhook reads both shapes but a pinned version avoids surprises).

    Returns ``{"status": ..., "hosted_invoice_url": ...}``.
    """
    if not settings.STRIPE_SECRET_KEY:
        raise SchoolBillingError("STRIPE_SECRET_KEY is not configured")

    resolved_price_id = _select_school_price_id(school, price_id)

    # Lock the school row BEFORE the existence check and the Stripe calls so two
    # concurrent POSTs cannot both pass the guard and double-bill. The lock is
    # released when the caller commits/rolls back the request transaction.
    locked_school = await lock_school_access_async(session, school.wriveted_identifier)
    if locked_school is not None:
        school = locked_school

    # One open subscription per school: refuse if ANY live subscription already
    # controls access — a card/invoice Stripe sub OR a comp grant (including the
    # empty-customer invoice_pending grant, which the Stripe-only predicate can't
    # see) — so a repeat POST does not issue a second invoice.
    existing_live = (
        await session.execute(
            select(Subscription.id)
            .where(
                Subscription.school_id == school.wriveted_identifier,
                Subscription.is_active.is_(True),
            )
            .limit(1)
        )
    ).first()
    if existing_live is not None:
        raise SchoolInvoiceConflictError(
            "School already has an active subscription or pending invoice grant"
        )

    stripe.api_key = settings.STRIPE_SECRET_KEY
    wriveted_id = str(school.wriveted_identifier)
    known_customer_id = await known_stripe_customer_id_async(
        session, school.wriveted_identifier
    )

    # Deterministic idempotency keys keyed on the school so a retried/duplicated
    # request reuses the same Stripe Customer + Subscription instead of creating
    # duplicates (and double-emitting invoices).
    idempotency_key = f"invoice-sub-{wriveted_id}"

    customer_params: dict = {
        "email": billing_email,
        "name": billing_name or school.name,
        "metadata": {"wriveted_school_id": wriveted_id},
    }
    if po_number:
        customer_params["invoice_settings"] = {
            "custom_fields": [{"name": "PO number", "value": po_number}]
        }

    def _create_on_stripe():
        if known_customer_id:
            # Reuse the existing Customer as-is; do NOT overwrite its contact
            # (email/name) — it may belong to a prior payer (contribution/lapsed
            # sub) and clobbering it would misdirect their invoices/receipts.
            customer_id = known_customer_id
        else:
            customer = stripe.Customer.create(
                idempotency_key=f"{idempotency_key}-customer",
                **customer_params,
            )
            customer_id = customer.id
        subscription = stripe.Subscription.create(
            customer=customer_id,
            items=[{"price": resolved_price_id}],
            collection_method="send_invoice",
            days_until_due=settings.INVOICE_DAYS_UNTIL_DUE,
            metadata={"wriveted_school_id": wriveted_id},
            expand=["latest_invoice"],
            idempotency_key=f"{idempotency_key}-subscription",
        )
        return customer_id, subscription

    try:
        customer_id, subscription = await asyncio.to_thread(_create_on_stripe)
    except Exception as e:
        logger.error(
            "Failed to create school invoice subscription",
            wriveted_school_id=wriveted_id,
            error=str(e),
        )
        raise SchoolBillingError(f"Could not create invoice subscription: {e}")

    latest_invoice = subscription.get("latest_invoice")
    hosted_invoice_url = (
        latest_invoice.get("hosted_invoice_url") if latest_invoice else None
    )

    # Immediate net-terms access, committed by the caller in the same request.
    grace = timedelta(
        days=settings.INVOICE_DAYS_UNTIL_DUE + settings.INVOICE_PENDING_GRACE_DAYS
    )
    await grant_invoice_pending_access(session, school, datetime.utcnow() + grace)

    logger.info(
        "Created school invoice subscription",
        wriveted_school_id=wriveted_id,
        stripe_subscription_id=subscription.id,
        stripe_customer_id=customer_id,
        po_number=po_number,
    )
    return {"status": "invoice_sent", "hosted_invoice_url": hosted_invoice_url}

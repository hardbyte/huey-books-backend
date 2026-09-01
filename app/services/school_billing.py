"""Stripe Checkout for school subscriptions.

Creates a Checkout Session (subscription mode) for the flat annual school
price. The session is scoped to the school via ``client_reference_id`` (the
webhook resolves schools from it), so the returned URL can be paid by the
school admin or forwarded to a sponsor (parent, library) — the subscription
attaches to the school regardless of who pays.
"""

import asyncio
from datetime import datetime, timedelta
from uuid import uuid4

import stripe
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from stripe import APIConnectionError, APIError, IdempotencyError, StripeError
from structlog import get_logger

from app.config import get_settings
from app.models.school import School
from app.models.school_billing import (
    SchoolBillingAccount,
    SchoolBillingAttempt,
    SchoolBillingAttemptStatus,
    SchoolBillingMethod,
)
from app.repositories.school_billing_repository import school_billing_repository
from app.schemas.school_billing import SchoolBillingStartResult
from app.services.school_access import (
    SchoolNotFoundError,
    grant_invoice_pending_access,
    has_blocking_billing_obligation_async,
    lock_school_access_async,
)
from app.services.school_billing_status import (
    recompute_school_access,
    resolve_school_billing_status,
    select_school_price_id,
)
from app.services.stripe_fields import stripe_field

logger = get_logger()
settings = get_settings()


class SchoolBillingError(Exception):
    """Raised when a school checkout session cannot be created."""


class SchoolBillingConflictError(SchoolBillingError):
    """The school already has a blocking billing obligation."""


def _is_definite_stripe_failure(error: BaseException) -> bool:
    """Whether a Stripe exception means Stripe *definitively* rejected the call.

    A definite rejection (e.g. ``CardError``, ``InvalidRequestError``, an
    idempotency conflict) is a response from Stripe saying it processed and
    refused the request, so no collectible subscription or charge was created
    for that operation and the attempt's open-collectible slot can be released
    for an immediate retry.

    Left as uncertain — and therefore requiring staff review — are a network
    error (``APIConnectionError``: the request may or may not have reached
    Stripe), a server-side ``APIError`` (5xx: Stripe may have created the
    resource before failing to respond), and any non-Stripe exception.
    """
    return isinstance(error, StripeError) and not isinstance(
        error, (APIConnectionError, APIError, IdempotencyError)
    )


async def _fail_attempt_on_definite_stripe_error(
    session: AsyncSession,
    attempt: SchoolBillingAttempt,
    error: BaseException,
    *,
    failure_reason: str,
) -> None:
    """Mark the reserved attempt FAILED when Stripe definitively rejected it.

    Freeing the open-collectible slot lets an admin retry immediately instead of
    being blocked by the "uncertain Stripe result requires staff review" path.
    An uncertain result is deliberately left CREATING so the slot stays held.
    """
    if not _is_definite_stripe_failure(error):
        return
    attempt.status = SchoolBillingAttemptStatus.FAILED
    attempt.failure_reason = failure_reason
    await session.commit()


def _validate_attempt_replay(
    attempt: SchoolBillingAttempt,
    *,
    method: SchoolBillingMethod,
    billing_email: str | None,
    billing_name: str | None,
    po_number: str | None,
) -> None:
    if attempt.method != method:
        raise SchoolBillingConflictError(
            "Idempotency key was already used for another billing method"
        )
    if method == SchoolBillingMethod.INVOICE and (
        attempt.billing_email != billing_email
        or attempt.billing_name != billing_name
        or attempt.purchase_order_number != po_number
    ):
        raise SchoolBillingConflictError(
            "Idempotency key was already used with different invoice details"
        )
    if (
        attempt.status == SchoolBillingAttemptStatus.CREATING
        and attempt.expires_at is not None
        and attempt.expires_at <= datetime.utcnow()
    ):
        raise SchoolBillingConflictError(
            "A previous billing request has an uncertain Stripe result and requires staff review"
        )


async def _reserve_attempt(
    session: AsyncSession,
    school: School,
    *,
    method: SchoolBillingMethod,
    client_idempotency_key: str | None,
    billing_email: str | None = None,
    billing_name: str | None = None,
    po_number: str | None = None,
) -> SchoolBillingAttempt:
    client_key = client_idempotency_key or str(uuid4())
    existing = await school_billing_repository.get_attempt_by_client_key(
        session, school.wriveted_identifier, client_key
    )
    if existing is not None:
        _validate_attempt_replay(
            existing,
            method=method,
            billing_email=billing_email,
            billing_name=billing_name,
            po_number=po_number,
        )
        return existing

    locked_school = await lock_school_access_async(session, school.wriveted_identifier)
    if locked_school is None:
        raise SchoolNotFoundError
    school = locked_school

    # The school lock serializes admission. Re-read because another request may
    # have committed an attempt while this request waited.
    existing = await school_billing_repository.get_attempt_by_client_key(
        session, school.wriveted_identifier, client_key
    )
    if existing is not None:
        await session.commit()
        _validate_attempt_replay(
            existing,
            method=method,
            billing_email=billing_email,
            billing_name=billing_name,
            po_number=po_number,
        )
        return existing

    now = datetime.utcnow()
    open_attempt = await school_billing_repository.get_open_attempt(
        session, school.wriveted_identifier
    )
    if (
        open_attempt is not None
        and open_attempt.expires_at is not None
        and open_attempt.expires_at <= now
    ):
        if open_attempt.status == SchoolBillingAttemptStatus.CREATING:
            await session.commit()
            raise SchoolBillingConflictError(
                "A previous billing request has an uncertain Stripe result and requires staff review"
            )
        else:
            open_attempt.status = SchoolBillingAttemptStatus.EXPIRED
            await session.flush()
            open_attempt = None
    if open_attempt is not None:
        await session.commit()
        if open_attempt.method != method:
            raise SchoolBillingConflictError(
                "School already has another billing attempt in progress"
            )
        return open_attempt
    if await has_blocking_billing_obligation_async(session, school.wriveted_identifier):
        raise SchoolBillingConflictError(
            "School already has an active subscription or pending invoice"
        )

    try:
        configured_price_id = select_school_price_id(school)
    except ValueError as error:
        raise SchoolBillingError(str(error)) from error
    attempt = SchoolBillingAttempt(
        school_id=school.wriveted_identifier,
        method=method,
        status=SchoolBillingAttemptStatus.CREATING,
        client_idempotency_key=client_key,
        configured_price_id=configured_price_id,
        billing_email=billing_email,
        billing_name=billing_name,
        purchase_order_number=po_number,
        invoice_days_until_due=(
            settings.INVOICE_DAYS_UNTIL_DUE
            if method == SchoolBillingMethod.INVOICE
            else None
        ),
        # Keep the recoverable CREATING window within Stripe API v1's 24-hour
        # idempotency retention. Invoice terms replace this once Stripe has
        # created the subscription.
        expires_at=now + timedelta(hours=23),
    )
    try:
        await school_billing_repository.add_attempt(session, attempt)
        await session.commit()
    except IntegrityError:
        await session.rollback()
        winner = await school_billing_repository.get_attempt_by_client_key(
            session, school.wriveted_identifier, client_key
        ) or await school_billing_repository.get_open_attempt(
            session, school.wriveted_identifier
        )
        if winner is None:
            raise
        return winner
    return attempt


async def _ensure_school_billing_customer(
    session: AsyncSession, school: School, attempt: SchoolBillingAttempt
) -> str:
    school_id = school.wriveted_identifier
    account = await school_billing_repository.get_account(session, school_id)
    if account is not None:
        return account.stripe_customer_id

    onboarding = (school.info or {}).get("onboarding") or {}
    # Re-read immediately before creating a Stripe Customer: under READ COMMITTED
    # this sees an account a concurrent request committed since the check above,
    # avoiding the common orphaned-Customer case. A rare orphan can still occur
    # if the concurrent commit lands between this read and add_account below (the
    # IntegrityError fallback recovers the winner's id but leaves that Customer
    # orphaned); that residual is acceptable.
    account = await school_billing_repository.get_account(session, school_id)
    if account is not None:
        return account.stripe_customer_id

    customer = await asyncio.to_thread(
        stripe.Customer.create,
        email=attempt.billing_email or onboarding.get("contact_email"),
        name=attempt.billing_name or school.name,
        metadata={
            "wriveted_school_id": str(school_id),
        },
        idempotency_key=f"{attempt.id}:customer-create",
    )
    account = SchoolBillingAccount(
        school_id=school_id,
        stripe_customer_id=customer.id,
    )
    attempt.stripe_customer_id = customer.id
    try:
        await school_billing_repository.add_account(session, account)
        await session.commit()
        return customer.id
    except IntegrityError:
        await session.rollback()
        await session.refresh(attempt)
        account = await school_billing_repository.get_account(session, school_id)
        if account is None:
            raise
        return account.stripe_customer_id


def _start_result(attempt: SchoolBillingAttempt) -> SchoolBillingStartResult:
    return SchoolBillingStartResult(
        attempt_id=attempt.id,
        method=attempt.method,
        status=attempt.status,
        checkout_url=attempt.checkout_url,
        hosted_invoice_url=attempt.hosted_invoice_url,
    )


async def create_school_checkout_session(
    school: School,
    *,
    session: AsyncSession,
    client_idempotency_key: str | None = None,
) -> SchoolBillingStartResult:
    """Durably start or replay a card Checkout attempt."""
    if not settings.STRIPE_SECRET_KEY:
        raise SchoolBillingError("STRIPE_SECRET_KEY is not configured")

    stripe.api_key = settings.STRIPE_SECRET_KEY
    attempt = await _reserve_attempt(
        session,
        school,
        method=SchoolBillingMethod.CARD,
        client_idempotency_key=client_idempotency_key,
    )
    if attempt.method != SchoolBillingMethod.CARD:
        return _start_result(attempt)
    if attempt.status != SchoolBillingAttemptStatus.CREATING:
        return _start_result(attempt)

    customer_id = await _ensure_school_billing_customer(session, school, attempt)
    app_url = settings.HUEY_BOOKS_APP_URL.rstrip("/")
    wriveted_id = str(school.wriveted_identifier)
    params = {
        "mode": "subscription",
        "line_items": [{"price": attempt.configured_price_id, "quantity": 1}],
        "client_reference_id": wriveted_id,
        "success_url": f"{app_url}/school/onboarding/success?session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{app_url}/school/onboarding/cancelled",
        "customer": customer_id,
        "metadata": {
            "wriveted_school_id": wriveted_id,
            "school_billing_attempt_id": str(attempt.id),
        },
        "subscription_data": {
            "metadata": {
                "wriveted_school_id": wriveted_id,
                "school_billing_attempt_id": str(attempt.id),
            }
        },
        "allow_promotion_codes": True,
        "expires_at": int(attempt.expires_at.timestamp()),
        "idempotency_key": f"{attempt.id}:checkout-session",
    }

    try:
        checkout_session = await asyncio.to_thread(
            stripe.checkout.Session.create, **params
        )
    except Exception as e:
        logger.error(
            "Failed to create school checkout session",
            wriveted_school_id=wriveted_id,
            error=str(e),
        )
        await _fail_attempt_on_definite_stripe_error(
            session, attempt, e, failure_reason="stripe_checkout_create_failed"
        )
        raise SchoolBillingError("Could not create checkout session. Please try again.")

    attempt.stripe_customer_id = customer_id
    attempt.stripe_checkout_session_id = checkout_session.id
    attempt.checkout_url = checkout_session.url
    attempt.status = SchoolBillingAttemptStatus.CHECKOUT_OPEN
    await session.commit()

    logger.info(
        "Created school checkout session",
        wriveted_school_id=wriveted_id,
        checkout_session_id=checkout_session.id,
    )
    return _start_result(attempt)


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
        raise SchoolBillingError(
            "Could not create contribution checkout session. Please try again."
        )

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

    billing_status = await resolve_school_billing_status(session, school)
    account = await school_billing_repository.get_account(
        session, school.wriveted_identifier
    )
    if billing_status.paid_subscription is None or account is None:
        return None

    stripe.api_key = settings.STRIPE_SECRET_KEY
    # Return to the admin UI, where the "Manage Subscription" button lives — the
    # consumer site has no /school/{id} route.
    admin_url = settings.SCHOOL_ADMIN_URL.rstrip("/")
    return_url = f"{admin_url}/school/{school.wriveted_identifier}"

    try:
        portal_session = await asyncio.to_thread(
            stripe.billing_portal.Session.create,
            customer=account.stripe_customer_id,
            return_url=return_url,
        )
    except Exception as e:
        logger.error(
            "Failed to create billing portal session",
            wriveted_school_id=str(school.wriveted_identifier),
            error=str(e),
        )
        raise SchoolBillingError(
            "Could not create billing portal session. Please try again."
        )

    return portal_session.url


async def create_school_invoice_subscription(
    session: AsyncSession,
    school: School,
    *,
    billing_email: str,
    billing_name: str | None = None,
    po_number: str | None = None,
    client_idempotency_key: str | None = None,
) -> SchoolBillingStartResult:
    """Create a fully-automated net-terms invoice subscription for a school.

    Zero manual staff steps: Stripe finalises + emails the invoice, sends the
    reminder schedule, collects payment, and renews the same way each period.
    The school is granted immediate net-terms access via an ``invoice_pending``
    comp grant (retired on payment, lapsed by the sweep if never paid).

    These Stripe **Dashboard** settings improve the experience but are not relied
    on for correctness: *email finalized invoices* + a *reminder schedule*
    (Billing → Subscriptions and emails) drive dunning, and *cancel the
    subscription when an invoice is N days overdue* emits
    ``customer.subscription.deleted``. Never-paid access does not hinge on them:
    a voided / uncollectible invoice retires the ``invoice_pending`` grant and
    drops the school directly (see ``deactivate_school_on_non_payment_sync``).

    The webhook/account API version should be pinned (invoice→subscription moved
    to ``invoice.parent.subscription_details.subscription`` on 2025+ versions;
    the webhook reads both shapes but a pinned version avoids surprises).

    Returns the persisted attempt state. An open invoice is not a paid
    entitlement; payment is established only by ``invoice.paid``.
    """
    if not settings.STRIPE_SECRET_KEY:
        raise SchoolBillingError("STRIPE_SECRET_KEY is not configured")

    persisted_school = await session.get(School, school.id)
    if persisted_school is None:
        raise SchoolNotFoundError
    school = persisted_school

    stripe.api_key = settings.STRIPE_SECRET_KEY
    attempt = await _reserve_attempt(
        session,
        school,
        method=SchoolBillingMethod.INVOICE,
        client_idempotency_key=client_idempotency_key,
        billing_email=billing_email,
        billing_name=billing_name,
        po_number=po_number,
    )
    if attempt.method != SchoolBillingMethod.INVOICE:
        return _start_result(attempt)
    if attempt.status != SchoolBillingAttemptStatus.CREATING:
        return _start_result(attempt)

    wriveted_id = str(school.wriveted_identifier)
    customer_id = await _ensure_school_billing_customer(session, school, attempt)
    attempt_billing_email = attempt.billing_email
    if attempt_billing_email is None:
        raise SchoolBillingError("Invoice attempt has no billing email")
    attempt_billing_name = attempt.billing_name
    attempt_po_number = attempt.purchase_order_number

    def _create_on_stripe():
        stripe.Customer.modify(
            customer_id,
            email=attempt_billing_email,
            name=attempt_billing_name or school.name,
            idempotency_key=f"{attempt.id}:customer-update",
        )
        subscription = stripe.Subscription.create(
            customer=customer_id,
            items=[{"price": attempt.configured_price_id}],
            collection_method="send_invoice",
            days_until_due=attempt.invoice_days_until_due,
            metadata={
                "wriveted_school_id": wriveted_id,
                "school_billing_attempt_id": str(attempt.id),
                **(
                    {"purchase_order_number": attempt_po_number}
                    if attempt_po_number is not None
                    else {}
                ),
            },
            expand=["latest_invoice"],
            idempotency_key=f"{attempt.id}:invoice-subscription",
        )
        latest_invoice = stripe_field(subscription, "latest_invoice")
        invoice_id = (
            latest_invoice.get("id")
            if hasattr(latest_invoice, "get")
            else latest_invoice
        )
        return subscription, latest_invoice, invoice_id

    try:
        subscription, latest_invoice, invoice_id = await asyncio.to_thread(
            _create_on_stripe
        )
    except Exception as e:
        logger.error(
            "Failed to create school invoice subscription",
            wriveted_school_id=wriveted_id,
            error=str(e),
        )
        await _fail_attempt_on_definite_stripe_error(
            session,
            attempt,
            e,
            failure_reason="stripe_invoice_subscription_create_failed",
        )
        raise SchoolBillingError(
            "Could not create invoice subscription. Please try again."
        )

    if attempt_po_number and invoice_id:
        try:
            await asyncio.to_thread(
                stripe.Invoice.modify,
                invoice_id,
                custom_fields=[{"name": "PO number", "value": attempt_po_number}],
                idempotency_key=f"{attempt.id}:invoice-po",
            )
        except Exception as error:
            logger.error(
                "Failed to attach purchase order number to school invoice",
                wriveted_school_id=wriveted_id,
                stripe_subscription_id=subscription.id,
                stripe_invoice_id=invoice_id,
                error=str(error),
            )

    hosted_invoice_url = (
        latest_invoice.get("hosted_invoice_url") if latest_invoice else None
    )

    attempt.stripe_customer_id = customer_id
    attempt.stripe_subscription_id = subscription.id
    attempt.stripe_invoice_id = invoice_id
    attempt.hosted_invoice_url = hosted_invoice_url
    attempt.status = SchoolBillingAttemptStatus.INVOICE_OPEN
    attempt.expires_at = datetime.utcnow() + timedelta(
        days=(attempt.invoice_days_until_due or settings.INVOICE_DAYS_UNTIL_DUE)
        + settings.INVOICE_PENDING_GRACE_DAYS
    )
    await grant_invoice_pending_access(
        session,
        school,
        attempt.expires_at,
        billing_attempt_id=attempt.id,
    )
    await recompute_school_access(session, school)
    await session.commit()

    logger.info(
        "Created school invoice subscription",
        wriveted_school_id=wriveted_id,
        stripe_subscription_id=subscription.id,
        stripe_customer_id=customer_id,
        po_number=attempt_po_number,
    )
    return _start_result(attempt)

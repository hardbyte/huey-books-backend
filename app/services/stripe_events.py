import hashlib
import json
from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID

import stripe
from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from stripe import Customer as StripeCustomer
from stripe import Price as StripePrice
from stripe import Product as StripeProduct
from stripe import Subscription as StripeSubscription
from structlog import get_logger
from structlog.contextvars import bind_contextvars

from app import crud
from app.config import get_settings
from app.db.session import get_session_maker
from app.models import School, User
from app.models.event import EventSlackChannel
from app.models.product import Product
from app.models.school import SchoolState
from app.models.school_billing import (
    SchoolBillingAttempt,
    SchoolBillingAttemptStatus,
    StripeEventReceipt,
)
from app.models.stripe_contribution import StripeContributionReceipt
from app.models.subscription import Subscription, SubscriptionType
from app.models.user import UserAccountType
from app.repositories.product_repository import product_repository
from app.repositories.school_repository import school_repository
from app.repositories.subscription_repository import subscription_repository
from app.schemas.product import ProductCreateIn
from app.schemas.subscription import SubscriptionCreateIn
from app.services.email_notification import EmailType, send_email_reliable_sync
from app.services.events import create_event
from app.services.school_access import (
    COMP_GRANT_SOURCES,
    CONTRIBUTION_GRANT_SOURCE,
    RETIREABLE_SOURCES,
    active_stripe_subscription_stmt,
    deactivate_school_on_non_payment_sync,
    ensure_comp_product_sync,
    invoice_pending_grant_id,
    lock_school_access_sync,
)
from app.services.school_billing_status import recompute_school_access_sync
from app.services.school_emails import (
    render_contribution_thankyou_html,
    render_school_activated_html,
    render_school_contribution_notice_html,
    render_school_renewal_reminder_html,
)
from app.services.stripe_price_cache import invalidate_price_cache

logger = get_logger()
settings = get_settings()

# Metadata marker set on a contribution Checkout Session (see school_billing);
# the webhook routes strictly on this, not on checkout mode.
CONTRIBUTION_METADATA_KIND = "school_contribution"
# Title of the audit event recorded per processed contribution.
CONTRIBUTION_EVENT_TITLE = "School contribution received"
# Synthetic Product + Subscription id prefix for contribution grants.
CONTRIBUTION_GRANT_PRODUCT_ID = "comp_school_contribution"
CONTRIBUTION_GRANT_PRODUCT_NAME = "School contribution (comped)"
CONTRIBUTION_GRANT_SUBSCRIPTION_PREFIX = "comp_contribution_"


def process_stripe_event(
    event_type: str,
    event_data: dict,
    *,
    event_id: str | None = None,
    event_created: int | None = None,
    api_version: str | None = None,
) -> dict[str, str]:
    """Claim and apply one Stripe event in a single database transaction."""
    logger.info("Processing a stripe event", event_type=event_type, event_id=event_id)
    bind_contextvars(stripe_event_type=event_type)
    event_created_at = (
        datetime.utcfromtimestamp(event_created) if event_created is not None else None
    )
    # Exactly-once guard. Events queued during the rollout window may lack an
    # event_id; fall back to a deterministic hash of the payload so redelivery is
    # still deduplicated rather than re-running handlers (duplicate emails/alerts).
    receipt_key = (
        event_id
        or "sha256:"
        + hashlib.sha256(
            json.dumps(
                {"type": event_type, "data": event_data}, sort_keys=True, default=str
            ).encode()
        ).hexdigest()
    )
    Session = get_session_maker()
    with Session.begin() as session:
        claimed_event_id = session.execute(
            pg_insert(StripeEventReceipt)
            .values(
                event_id=receipt_key,
                event_type=event_type,
                event_created_at=event_created_at,
                api_version=api_version,
            )
            .on_conflict_do_nothing(index_elements=[StripeEventReceipt.event_id])
            .returning(StripeEventReceipt.event_id)
        ).scalar_one_or_none()
        if claimed_event_id is None:
            logger.info("Ignoring duplicate Stripe event", event_id=receipt_key)
            return {"status": "duplicate"}
        if not _handle_durable_school_billing_event(
            session, event_type, event_data, event_created_at
        ):
            _dispatch_stripe_event(session, event_type, event_data)
    return {"status": "processed"}


def _dispatch_stripe_event(session, event_type: str, event_data: dict) -> None:
    if event_type in (
        "price.created",
        "price.updated",
        "price.deleted",
        "product.created",
        "product.updated",
    ):
        _handle_price_catalog_event(event_type, event_data)
        return
    if event_type in (
        "checkout.session.completed",
        "checkout.session.async_payment_succeeded",
    ) and _is_contribution_checkout(event_data):
        contribution_school = _resolve_school_from_client_reference(session, event_data)
        _handle_contribution_checkout_completed(
            session, contribution_school, event_data
        )
        return

    wriveted_user, school, _ = _extract_user_and_customer_from_stripe_object(
        session, event_data, event_data.get("object")
    )
    match event_type:
        case "invoice.paid":
            _handle_invoice_paid(session, wriveted_user, school, event_data)
        case "invoice.upcoming":
            _handle_invoice_upcoming(session, event_data)
        case "invoice.finalized":
            _handle_invoice_finalized(session, school, event_data)
        case "invoice.voided" | "invoice.marked_uncollectible":
            _handle_invoice_not_collected(
                session, wriveted_user, school, event_type, event_data
            )
        case "invoice.payment_failed":
            _handle_invoice_payment_failed(session, wriveted_user, school, event_data)
        case "checkout.session.completed" | "checkout.session.async_payment_succeeded":
            _handle_checkout_session_completed(
                session, wriveted_user, school, event_data
            )
        case "customer.subscription.updated":
            _handle_subscription_updated(session, wriveted_user, school, event_data)
        case "customer.subscription.deleted":
            _handle_subscription_cancelled(session, wriveted_user, school, event_data)
        case "customer.subscription.created":
            _handle_subscription_created(session, wriveted_user, school, event_data)
        case "customer.created" | "customer.updated" | "payment_intent.succeeded":
            logger.info(
                "Stripe event requires no local transition", event_type=event_type
            )
        case "payment_intent.payment_failed":
            logger.warning("Payment failed")
        case _:
            logger.info("Unhandled Stripe event", event_type=event_type)


def _handle_price_catalog_event(event_type: str, event_data: dict) -> None:
    """Invalidate the cached Stripe price terms when a price/product changes.

    Cheap and cache-only (no DB writes). A price event names the affected price,
    so invalidate just that entry; a product event does not, so clear the whole
    cache (any of its prices may have moved).
    """
    if event_type.startswith("price."):
        price_id = event_data.get("id")
        invalidate_price_cache(price_id)
        logger.info("Invalidated cached Stripe price", price_id=price_id)
    else:
        invalidate_price_cache()
        logger.info("Invalidated all cached Stripe prices after product change")


def _handle_durable_school_billing_event(
    session,
    event_type: str,
    event_data: dict,
    event_created_at: datetime | None,
) -> bool:
    attempt = _billing_attempt_for_event(session, event_type, event_data)
    authoritative_subscription = None
    prefetched_local_subscription = None
    if (
        event_type
        in {
            "checkout.session.completed",
            "checkout.session.async_payment_succeeded",
        }
        and attempt is not None
        and not _is_contribution_checkout(event_data)
    ):
        subscription_id = _stripe_id(event_data.get("subscription"))
        if subscription_id is not None:
            authoritative_subscription = _retrieve_subscription(
                subscription_id, event_data
            )
    elif event_type == "invoice.paid":
        subscription_id = _invoice_subscription_id(event_data)
        prefetched_local_subscription = (
            subscription_repository.get_by_id(session, subscription_id)
            if subscription_id
            else None
        )
        if attempt is not None or (
            prefetched_local_subscription is not None
            and prefetched_local_subscription.school_id is not None
        ):
            authoritative_subscription = _retrieve_subscription(
                subscription_id, event_data
            )
    elif event_type in {
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    }:
        prefetched_local_subscription = subscription_repository.get_by_id(
            session, event_data.get("id")
        )
        authoritative_subscription = _retrieve_subscription(
            event_data.get("id"), event_data
        )

    if (
        authoritative_subscription is not None
        and event_type != "customer.subscription.deleted"
    ):
        items = (authoritative_subscription.get("items") or {}).get("data") or []
        price_id = (items[0].get("price") or {}).get("id") if items else None
        if price_id is not None:
            # Product synchronization can call Stripe. Complete it before any
            # school/attempt row lock is acquired.
            _sync_stripe_price_with_wriveted_product(session, price_id)

    locked_school: School | None = None
    if attempt is not None:
        locked_school = lock_school_access_sync(session, attempt.school_id)
        if locked_school is None:
            return True
        attempt = session.execute(
            select(SchoolBillingAttempt)
            .where(SchoolBillingAttempt.id == attempt.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one()

    if event_type in {
        "checkout.session.expired",
        "checkout.session.async_payment_failed",
    }:
        if (
            attempt is not None
            and attempt.status != SchoolBillingAttemptStatus.PAID
            and not _attempt_event_is_stale(attempt, event_created_at)
        ):
            attempt.status = (
                SchoolBillingAttemptStatus.EXPIRED
                if event_type == "checkout.session.expired"
                else SchoolBillingAttemptStatus.FAILED
            )
            _advance_event_watermark(attempt, event_created_at)
        return True

    if event_type in {
        "checkout.session.completed",
        "checkout.session.async_payment_succeeded",
    }:
        if attempt is None or _is_contribution_checkout(event_data):
            return False
        if _attempt_event_is_stale(attempt, event_created_at):
            return True
        subscription_id = _stripe_id(event_data.get("subscription"))
        if subscription_id is None:
            return True
        stripe_subscription = authoritative_subscription
        if stripe_subscription is None:
            return True
        paid = event_data.get("payment_status") in {"paid", "no_payment_required"}
        subscription = _upsert_school_subscription(
            session,
            attempt,
            stripe_subscription,
            event_created_at,
            paid=paid,
            checkout_session_id=event_data.get("id"),
        )
        attempt.stripe_subscription_id = subscription.id
        _advance_event_watermark(attempt, event_created_at)
        if paid:
            attempt.status = SchoolBillingAttemptStatus.PAID
            _retire_comp_grants(session, attempt.school_id)
        school = locked_school
        if school is None:
            return True
        recompute_school_access_sync(session, school)
        _record_school_billing_event(session, school, event_type, event_data, attempt)
        return True

    if event_type == "invoice.paid":
        subscription_id = _invoice_subscription_id(event_data)
        local_subscription = prefetched_local_subscription
        if attempt is None and (
            local_subscription is None or local_subscription.school_id is None
        ):
            return False
        if attempt is not None and _attempt_event_is_stale(attempt, event_created_at):
            return True
        stripe_subscription = authoritative_subscription
        if stripe_subscription is None:
            return False
        school_id = (
            attempt.school_id if attempt is not None else local_subscription.school_id
        )
        school = locked_school or lock_school_access_sync(session, school_id)
        if school is None:
            return True
        if local_subscription is not None:
            # Row-lock the local subscription, but do NOT suppress the payment by
            # the status watermark: invoice.paid is authoritative for payment and a
            # later customer.subscription.updated must not shadow it. The upsert is
            # non-regressing (paid_at set-once, expiration only advances), so an
            # out-of-order or redelivered invoice.paid is safe/idempotent.
            local_subscription = session.execute(
                select(Subscription)
                .where(Subscription.id == local_subscription.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).scalar_one()
        subscription = _upsert_school_subscription(
            session,
            attempt,
            stripe_subscription,
            event_created_at,
            paid=True,
            school=school,
        )
        if attempt is not None:
            attempt.status = SchoolBillingAttemptStatus.PAID
            attempt.stripe_invoice_id = event_data.get("id")
            attempt.stripe_subscription_id = subscription.id
            _advance_event_watermark(attempt, event_created_at)
        _retire_comp_grants(session, school.wriveted_identifier)
        recompute_school_access_sync(session, school)
        _record_school_billing_event(session, school, event_type, event_data, attempt)
        return True

    if event_type == "invoice.finalization_failed":
        if attempt is None:
            return False
        if attempt.status == SchoolBillingAttemptStatus.PAID:
            return True
        if _attempt_event_is_stale(attempt, event_created_at):
            return True
        finalization_error = event_data.get("last_finalization_error") or {}
        attempt.failure_reason = (
            finalization_error.get("message")
            or finalization_error.get("code")
            or "Stripe could not finalize the invoice"
        )
        _advance_event_watermark(attempt, event_created_at)
        school = locked_school
        if school is None:
            return True
        _record_school_billing_event(session, school, event_type, event_data, attempt)
        return True

    terminal_invoice_statuses = {
        "invoice.voided": SchoolBillingAttemptStatus.VOIDED,
        "invoice.marked_uncollectible": SchoolBillingAttemptStatus.UNCOLLECTIBLE,
    }
    if event_type in terminal_invoice_statuses:
        if attempt is None:
            return False
        if attempt.status == SchoolBillingAttemptStatus.PAID:
            return True
        if _attempt_event_is_stale(attempt, event_created_at):
            return True
        attempt.status = terminal_invoice_statuses[event_type]
        attempt.stripe_invoice_id = event_data.get("id")
        _advance_event_watermark(attempt, event_created_at)
        if attempt.stripe_subscription_id:
            subscription = subscription_repository.get_by_id(
                session, attempt.stripe_subscription_id
            )
            if subscription is not None and subscription.paid_at is None:
                subscription.is_active = False
                subscription.stripe_status = "unpaid"
                _advance_event_watermark(subscription, event_created_at)
        pending_grant = session.get(
            Subscription, invoice_pending_grant_id(attempt.school_id)
        )
        if pending_grant is not None and str(
            (pending_grant.info or {}).get("billing_attempt_id")
        ) == str(attempt.id):
            pending_grant.is_active = False
        school = locked_school
        if school is None:
            return True
        recompute_school_access_sync(session, school)
        _record_school_billing_event(session, school, event_type, event_data, attempt)
        return True

    if event_type in {
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    }:
        subscription_id = event_data.get("id")
        local_subscription = prefetched_local_subscription
        if attempt is None and (
            local_subscription is None or local_subscription.school_id is None
        ):
            return False
        school_id = (
            attempt.school_id if attempt is not None else local_subscription.school_id
        )
        school = locked_school or lock_school_access_sync(session, school_id)
        if school is None:
            return True
        if local_subscription is not None:
            local_subscription = session.execute(
                select(Subscription)
                .where(Subscription.id == local_subscription.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).scalar_one()
        if event_type == "customer.subscription.deleted":
            if local_subscription is not None:
                local_subscription.is_active = False
                local_subscription.stripe_status = (
                    event_data.get("status") or "canceled"
                )
                _advance_event_watermark(local_subscription, event_created_at)
                ended_at = event_data.get("ended_at")
                if ended_at is not None:
                    local_subscription.expiration = datetime.utcfromtimestamp(ended_at)
            if (
                attempt is not None
                and attempt.status != SchoolBillingAttemptStatus.PAID
            ):
                attempt.status = SchoolBillingAttemptStatus.CANCELLED
                _advance_event_watermark(attempt, event_created_at)
        else:
            # A created/updated event is informational: suppress it if an older
            # event arrives after a newer one. (The deleted branch above is
            # terminal and applied unconditionally, so a cancellation is never
            # shadowed by a later-timestamped update.)
            if local_subscription is not None and _subscription_event_is_stale(
                local_subscription, event_created_at
            ):
                return True
            stripe_subscription = authoritative_subscription
            if stripe_subscription is None:
                return True
            _upsert_school_subscription(
                session,
                attempt,
                stripe_subscription,
                event_created_at,
                paid=False,
                school=school,
            )
        recompute_school_access_sync(session, school)
        _record_school_billing_event(session, school, event_type, event_data, attempt)
        return True

    if event_type == "invoice.finalized" and attempt is not None:
        attempt.stripe_invoice_id = event_data.get("id")
        attempt.hosted_invoice_url = event_data.get("hosted_invoice_url")
        attempt.failure_reason = None
        _advance_event_watermark(attempt, event_created_at)
        return True
    return False


def _billing_attempt_for_event(
    session, event_type: str, event_data: dict
) -> SchoolBillingAttempt | None:
    metadata = event_data.get("metadata") or {}
    parent = event_data.get("parent") or {}
    subscription_details = parent.get("subscription_details") or {}
    metadata = {**(subscription_details.get("metadata") or {}), **metadata}
    attempt_id = metadata.get("school_billing_attempt_id")
    if attempt_id:
        try:
            attempt = session.get(SchoolBillingAttempt, UUID(str(attempt_id)))
        except ValueError:
            attempt = None
        if attempt is not None:
            return attempt

    object_id = event_data.get("id")
    subscription_id = (
        event_data.get("subscription")
        if event_type.startswith("checkout.session")
        else _invoice_subscription_id(event_data)
        if event_type.startswith("invoice.")
        else object_id
        if event_type.startswith("customer.subscription")
        else None
    )
    conditions = []
    if event_type.startswith("checkout.session") and object_id:
        conditions.append(SchoolBillingAttempt.stripe_checkout_session_id == object_id)
    if event_type.startswith("invoice.") and object_id:
        conditions.append(SchoolBillingAttempt.stripe_invoice_id == object_id)
    if subscription_id:
        subscription_condition = (
            SchoolBillingAttempt.stripe_subscription_id == _stripe_id(subscription_id)
        )
        if event_type.startswith("invoice."):
            subscription_condition = subscription_condition & (
                SchoolBillingAttempt.stripe_invoice_id.is_(None)
            )
        conditions.append(subscription_condition)
    if not conditions:
        return None
    return (
        session.execute(
            select(SchoolBillingAttempt)
            .where(or_(*conditions))
            .order_by(SchoolBillingAttempt.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def _retrieve_subscription(subscription_id: str | None, fallback: dict):
    if subscription_id is None:
        return fallback
    try:
        return StripeSubscription.retrieve(subscription_id)
    except Exception as error:
        logger.warning(
            "Could not retrieve authoritative Stripe subscription",
            subscription_id=subscription_id,
            error=str(error),
        )
        if fallback.get("object") == "subscription":
            return fallback
        raise


def _upsert_school_subscription(
    session,
    attempt: SchoolBillingAttempt | None,
    stripe_subscription,
    event_created_at: datetime | None,
    *,
    paid: bool,
    checkout_session_id: str | None = None,
    school: School | None = None,
) -> Subscription:
    subscription_id = _stripe_id(stripe_subscription.get("id")) or (
        attempt.stripe_subscription_id if attempt is not None else None
    )
    if subscription_id is None:
        raise ValueError("Stripe subscription id is required")
    if school is None and attempt is not None:
        school = session.execute(
            select(School).where(School.wriveted_identifier == attempt.school_id)
        ).scalar_one()
    if school is None:
        raise ValueError("School is required for a school subscription")

    items = (stripe_subscription.get("items") or {}).get("data") or []
    price_id = ((items[0].get("price") or {}).get("id") if items else None) or (
        attempt.configured_price_id if attempt is not None else None
    )
    if price_id is None:
        raise ValueError("Stripe price id is required")
    _sync_stripe_price_with_wriveted_product(session, price_id)
    period_end = stripe_subscription.get("current_period_end")
    if period_end is None and items:
        period_end = items[0].get("current_period_end")
    stripe_period_end = (
        datetime.utcfromtimestamp(period_end)
        if isinstance(period_end, (int, float))
        else period_end
        if isinstance(period_end, datetime)
        else datetime.utcnow()
    )
    existing = subscription_repository.get_by_id(session, subscription_id)
    if paid:
        # Set-once: keep the original payment time so a redelivered/out-of-order
        # invoice.paid does not rewrite it.
        paid_at = (
            existing.paid_at
            if existing is not None and existing.paid_at is not None
            else (event_created_at or datetime.utcnow())
        )
    elif existing is not None:
        paid_at = existing.paid_at
    else:
        paid_at = None
    # Expiration only advances. On payment take the later of the known paid-through
    # boundary and this invoice's period end (a stale/older invoice.paid can't
    # regress it). A subscription update can move current_period_end before a
    # renewal invoice is paid, so preserve the paid-through boundary until then.
    if paid:
        expiration = stripe_period_end
        if existing is not None and existing.expiration is not None:
            expiration = max(existing.expiration, stripe_period_end)
    elif existing is None or existing.paid_at is None:
        expiration = stripe_period_end
    else:
        expiration = existing.expiration
    status = stripe_subscription.get("status")
    return subscription_repository.upsert(
        session,
        SubscriptionCreateIn(
            id=subscription_id,
            type=SubscriptionType.SCHOOL,
            product_id=price_id,
            stripe_customer_id=str(
                _stripe_id(stripe_subscription.get("customer"))
                or (attempt.stripe_customer_id if attempt is not None else "")
            ),
            school_id=str(school.wriveted_identifier),
            is_active=(
                paid_at is not None and status in {"active", "past_due", "trialing"}
            ),
            expiration=expiration,
            latest_checkout_session_id=checkout_session_id,
            stripe_status=status,
            collection_method=stripe_subscription.get("collection_method"),
            paid_at=paid_at,
            last_stripe_event_created_at=(
                event_created_at
                or (existing.last_stripe_event_created_at if existing else None)
            ),
        ),
        commit=False,
    )


def _record_school_billing_event(
    session,
    school: School,
    event_type: str,
    event_data: dict,
    attempt: SchoolBillingAttempt | None,
) -> None:
    create_event(
        session=session,
        title="School billing transition",
        description=event_type,
        info={
            "stripe_event_type": event_type,
            "stripe_object_id": event_data.get("id"),
            "school_billing_attempt_id": str(attempt.id) if attempt else None,
        },
        school=school,
        commit=False,
        enable_processing=False,
        external_notifications=False,
    )


def _attempt_event_is_stale(
    attempt: SchoolBillingAttempt, event_created_at: datetime | None
) -> bool:
    return (
        event_created_at is not None
        and attempt.last_stripe_event_created_at is not None
        and event_created_at < attempt.last_stripe_event_created_at
    )


def _subscription_event_is_stale(
    subscription: Subscription, event_created_at: datetime | None
) -> bool:
    return (
        event_created_at is not None
        and subscription.last_stripe_event_created_at is not None
        and event_created_at < subscription.last_stripe_event_created_at
    )


def _advance_event_watermark(target, event_created_at: datetime | None) -> None:
    if event_created_at is not None and (
        target.last_stripe_event_created_at is None
        or event_created_at > target.last_stripe_event_created_at
    ):
        target.last_stripe_event_created_at = event_created_at


def _stripe_id(value) -> str | None:
    if isinstance(value, dict):
        return value.get("id")
    return str(value) if value else None


def _extract_user_and_customer_from_stripe_object(
    session, stripe_object, stripe_object_type
):
    logger.info(
        "Extracting user and customer from stripe object", stripe_object=stripe_object
    )

    wriveted_user = None
    school = None
    # webhook is only listening to events that are guaranteed to include a customer id (for now)
    stripe_customer = _get_stripe_customer_from_stripe_object(
        stripe_object, stripe_object_type
    )
    logger.info(
        "Got stripe customer from stripe object", stripe_customer=stripe_customer
    )

    # check customer metadata for a wriveted user id
    # (this is stored upon the first successful checkout)
    metadata = stripe_customer.get("metadata")
    stripe_customer_wriveted_id = metadata.get("wriveted_id") if metadata else None
    if stripe_customer_wriveted_id:
        wriveted_user = crud.user.get(session, stripe_customer_wriveted_id)
        logger.info("Found wriveted user id in customer metadata", user=wriveted_user)

    # check for any custom client_reference_id injected by our frontend (a Wriveted user id or school id)
    # note: empty values can sometimes be returned as the strings "undefined" or "null"
    client_reference_id = stripe_object.get("client_reference_id")
    if client_reference_id == "undefined" or client_reference_id == "null":
        client_reference_id = None

    if client_reference_id:
        if referenced_user := crud.user.get(session, client_reference_id):
            if wriveted_user and referenced_user != wriveted_user:
                logger.warning(
                    "Client reference id does not match User associated with Stripe customer id",
                    referenced_user_id=referenced_user.id,
                )
            else:
                wriveted_user = referenced_user
                logger.info(
                    "Client reference id matches User associated with Stripe customer id",
                    referenced_user_id=referenced_user.id,
                )
        elif school := school_repository.get_by_wriveted_id(
            session, wriveted_id=client_reference_id
        ):
            logger.info(
                "Client reference id matches School",
                school_id=school.wriveted_identifier,
                school_name=school.name,
            )
            bind_contextvars(
                school_id=school.wriveted_identifier, school_name=school.name
            )

        else:
            logger.warning(
                "Client reference id does not match any user",
                client_reference_id=client_reference_id,
            )

    # Fall back to metadata.wriveted_school_id (stamped on Customer and on
    # API-created — invoice — subscriptions), so schools resolve even when there
    # is no client_reference_id (invoice.* and customer.subscription.* events).
    if school is None:
        school = _resolve_school_from_metadata(
            session, stripe_object, stripe_object_type, stripe_customer
        )
        if school is not None:
            bind_contextvars(
                school_id=school.wriveted_identifier, school_name=school.name
            )

    if wriveted_user:
        bind_contextvars(wriveted_user_id=str(wriveted_user.id))

    return wriveted_user, school, stripe_customer


def _invoice_subscription_id(invoice: dict) -> Optional[str]:
    """Subscription id an invoice belongs to, across Stripe API versions.

    Pre-2025 API versions expose ``invoice.subscription``; 2025+ moved it to
    ``invoice.parent.subscription_details.subscription``. Read both so behaviour
    does not silently change on an API-version bump. The account/webhook API
    version should nonetheless be pinned.
    """

    def _id(value):
        if isinstance(value, dict):
            return value.get("id")
        return value or None

    direct = _id(invoice.get("subscription"))
    if direct:
        return direct
    parent = invoice.get("parent") or {}
    details = parent.get("subscription_details") or {}
    return _id(details.get("subscription"))


def _resolve_school_from_metadata(
    session, stripe_object, object_type, stripe_customer
) -> Optional[School]:
    """Resolve a school from ``metadata.wriveted_school_id`` on the customer, the
    subscription, or (for invoices) the invoice's subscription."""
    candidate_ids: list = []

    if stripe_customer is not None:
        cust_meta = stripe_customer.get("metadata") or {}
        candidate_ids.append(cust_meta.get("wriveted_school_id"))

    if object_type == "subscription":
        candidate_ids.append(
            (stripe_object.get("metadata") or {}).get("wriveted_school_id")
        )
    elif object_type == "invoice":
        subscription_id = _invoice_subscription_id(stripe_object)
        if subscription_id:
            try:
                stripe_subscription = StripeSubscription.retrieve(subscription_id)
                candidate_ids.append(
                    (stripe_subscription.get("metadata") or {}).get(
                        "wriveted_school_id"
                    )
                )
            except Exception as e:
                logger.warning(
                    "Could not retrieve invoice's subscription for metadata resolution",
                    subscription_id=subscription_id,
                    error=str(e),
                )

    for school_id in candidate_ids:
        if not school_id or school_id in ("undefined", "null"):
            continue
        school = school_repository.get_by_wriveted_id(session, wriveted_id=school_id)
        if school is not None:
            logger.info(
                "Resolved school from Stripe metadata",
                school_id=school.wriveted_identifier,
                school_name=school.name,
            )
            return school
    return None


def _stamp_customer_school_metadata(stripe_customer, school: School) -> None:
    """Best-effort stamp of ``metadata.wriveted_school_id`` on the Stripe Customer
    so later customer/subscription/invoice events resolve the school directly."""
    if stripe_customer is None or school is None:
        return
    metadata = stripe_customer.metadata or {}
    if metadata.get("wriveted_school_id") == str(school.wriveted_identifier):
        return
    try:
        stripe_customer.metadata["wriveted_school_id"] = str(school.wriveted_identifier)
        stripe_customer.save()
    except Exception as e:
        logger.warning(
            "Failed to stamp wriveted_school_id on Stripe customer",
            error=str(e),
        )


def _get_stripe_customer_from_stripe_object(stripe_object, stripe_object_type):
    if stripe_object_type == "customer":
        stripe_customer = stripe_object
    else:
        stripe_customer_id = stripe_object.get("customer")
        if stripe_customer_id:
            stripe_customer = StripeCustomer.retrieve(stripe_customer_id)
            bind_contextvars(stripe_customer_id=stripe_customer_id)
        else:
            raise NotImplementedError("Stripe event does not include a customer id")
    return stripe_customer


def _handle_invoice_paid(
    session, wriveted_user: User | None, school: School | None, event_data: dict
):
    logger.info("Invoice paid. Upserting subscription and activating school")
    # Read the subscription id across API-version shapes (a bare invoice with no
    # subscription — e.g. a one-off — has nothing to do here).
    stripe_subscription_id = _invoice_subscription_id(event_data)
    stripe_customer_id = event_data.get("customer")

    if stripe_subscription_id is None:
        logger.warning(
            "Invoice paid event does not include a subscription id. Ignoring"
        )
        return

    stripe_subscription = StripeSubscription.retrieve(stripe_subscription_id)

    # Resolve the school from the subscription's own metadata when the event did
    # not carry it (invoice events have no client_reference_id).
    if school is None:
        metadata_school_id = (stripe_subscription.get("metadata") or {}).get(
            "wriveted_school_id"
        )
        if metadata_school_id:
            school = school_repository.get_by_wriveted_id(
                session, wriveted_id=metadata_school_id
            )

    stripe_price_id = stripe_subscription["items"]["data"][0]["price"]["id"]
    _sync_stripe_price_with_wriveted_product(session, stripe_price_id)

    is_active = stripe_subscription.status in {"active", "past_due"}
    expiration = datetime.utcfromtimestamp(stripe_subscription.current_period_end)
    paid_at = datetime.utcnow()
    collection_method = stripe_subscription.get("collection_method")

    subscription = subscription_repository.get_by_id(
        session, subscription_id=stripe_subscription_id
    )
    if subscription is None:
        # An API-created (invoice) subscription reaches invoice.paid without a
        # prior checkout.session.completed, so upsert it here rather than warning
        # and dropping the activation.
        subscription = subscription_repository.upsert(
            session,
            SubscriptionCreateIn(
                id=stripe_subscription_id,
                type=(SubscriptionType.SCHOOL if school else SubscriptionType.FAMILY),
                product_id=stripe_price_id,
                stripe_customer_id=(
                    str(stripe_customer_id) if stripe_customer_id else ""
                ),
                school_id=str(school.wriveted_identifier) if school else None,
                is_active=is_active,
                expiration=expiration,
                stripe_status=stripe_subscription.status,
                collection_method=collection_method,
                paid_at=paid_at,
            ),
            commit=False,
        )
    else:
        subscription.expiration = expiration
        subscription.is_active = is_active
        subscription.stripe_status = stripe_subscription.status
        subscription.collection_method = collection_method
        subscription.paid_at = paid_at
        if school is not None and subscription.school_id is None:
            subscription.school_id = school.wriveted_identifier

    # If still unknown, recover the school from the (now-present) subscription row.
    if school is None and subscription.school_id:
        school = school_repository.get_by_wriveted_id(
            session, str(subscription.school_id)
        )

    # Activate the (invoice- or card-)paying school and retire any comp grant it
    # was riding on — including the invoice_pending net-terms grant. Both are
    # idempotent, so a redelivered invoice.paid is a safe no-op.
    if school is not None and is_active:
        _activate_school_after_payment(session, school, None)
        _retire_comp_grants(session, school.wriveted_identifier)
        recompute_school_access_sync(session, school)

    # Use unified event workflow instead of direct crud.event.create
    create_event(
        session=session,
        title="Subscription payment received",
        description="Invoice paid for subscription",
        info={
            "stripe_invoice_id": event_data.get("id"),
            "stripe_customer_id": stripe_customer_id,
            "stripe_subscription_id": stripe_subscription_id,
            "collection_method": stripe_subscription.get("collection_method"),
            "expiration": str(subscription.expiration),
        },
        school=school,
        account=wriveted_user,
        commit=False,
        enable_processing=False,  # No processing needed for payment received
        external_notifications=False,  # Internal accounting event
    )


def _handle_invoice_finalized(session, school: School | None, event_data: dict) -> None:
    """Record that an invoice was finalised and sent (audit only, no state change).

    Captures the hosted invoice URL so staff can find/forward it (useful for the
    net-terms invoice flow).
    """
    create_event(
        session=session,
        title="Invoice finalized",
        description="Stripe finalized and issued an invoice",
        info={
            "stripe_invoice_id": event_data.get("id"),
            "stripe_customer_id": event_data.get("customer"),
            "stripe_subscription_id": _invoice_subscription_id(event_data),
            "hosted_invoice_url": event_data.get("hosted_invoice_url"),
            "amount_due": event_data.get("amount_due"),
            "collection_method": event_data.get("collection_method"),
        },
        school=school,
        commit=False,
        enable_processing=False,
        external_notifications=False,
    )


def _handle_invoice_not_collected(
    session,
    wriveted_user: Optional[User],
    school: School | None,
    event_type: str,
    event_data: dict,
) -> None:
    """Handle a voided / uncollectible invoice as terminal non-payment.

    Belt-and-suspenders so never-paid access does not hinge on Stripe's
    "cancel the subscription when an invoice is N days overdue" setting being
    configured: for a school whose access rests on an unpaid invoice (i.e. no
    real *paid* Stripe subscription), retire the ``invoice_pending`` grant and
    drop the school to INACTIVE now. A voided renewal invoice on a school that
    IS paying (a live Stripe sub exists) is audit-only — its lifecycle stays with
    the subscription (``customer.subscription.deleted``).
    """
    create_event(
        session=session,
        title="Invoice not collected",
        description=f"Invoice {event_type.split('.')[-1]} (treated as non-payment)",
        info={
            "event_type": event_type,
            "stripe_invoice_id": event_data.get("id"),
            "stripe_customer_id": event_data.get("customer"),
            "stripe_subscription_id": _invoice_subscription_id(event_data),
        },
        school=school,
        account=wriveted_user,
        slack_channel=EventSlackChannel.MEMBERSHIPS,
        commit=False,
        enable_processing=False,
        external_notifications=True,
    )

    # Access lifecycle lives in the school_access service, not this webhook adapter.
    if school is not None:
        deactivate_school_on_non_payment_sync(session, school)


def _handle_invoice_payment_failed(
    session, wriveted_user: Optional[User], school: School | None, event_data: dict
) -> None:
    """Log a failed invoice payment and alert staff; no state change.

    Stripe runs its own dunning retries; a final give-up arrives as
    customer.subscription.deleted, which deactivates the school.
    """
    logger.warning(
        "Invoice payment failed",
        stripe_subscription=_invoice_subscription_id(event_data),
    )
    create_event(
        session=session,
        title="Invoice payment failed",
        description="A subscription invoice payment attempt failed",
        info={
            "stripe_invoice_id": event_data.get("id"),
            "stripe_customer_id": event_data.get("customer"),
            "stripe_subscription_id": _invoice_subscription_id(event_data),
            "attempt_count": event_data.get("attempt_count"),
            "next_payment_attempt": event_data.get("next_payment_attempt"),
        },
        school=school,
        account=wriveted_user,
        slack_channel=EventSlackChannel.MEMBERSHIPS,
        commit=False,
        enable_processing=False,
        external_notifications=True,
    )


def _handle_checkout_session_completed(
    session, wriveted_user: Optional[User], school: School | None, event_data: dict
) -> Optional[Subscription]:
    """

    # https://stripe.com/docs/api/checkout/sessions/object
    """
    logger.info("Checkout session completed. Creating subscription")
    # in this case we want to query the Stripe API for the subscription and customer,
    # as we know they exist and have been processed by Stripe.
    # we can then use this information to create a new subscription in our database (if needed),
    # and link the customer to the user or school (if needed).
    stripe_subscription_id = event_data.get("subscription")
    client_reference_id = event_data.get("client_reference_id")
    logger.info(
        "Client reference id from checkout session",
        client_reference_id=client_reference_id,
    )
    # Note this checkout complete could get fired for non-subscription purchases
    if stripe_subscription_id is None:
        logger.warning(
            "Checkout session completed for non-subscription purchase. Ignoring"
        )
        return

    stripe_subscription = StripeSubscription.retrieve(stripe_subscription_id)

    stripe_customer_id = stripe_subscription.customer
    stripe_customer = StripeCustomer.retrieve(stripe_customer_id)
    stripe_customer_email = stripe_customer.get("email")

    if not stripe_customer_email:
        logger.warning("Checkout session emitted without an email address")

    checkout_session_id = event_data.get("id")

    if wriveted_user and not stripe_customer.metadata.get("wriveted_id"):
        # we have a wriveted user, but no wriveted id on the stripe customer
        logger.info(
            "Updating Stripe customer metadata with Wriveted user id",
            stripe_customer_id=stripe_customer_id,
        )
        stripe_customer.metadata["wriveted_id"] = str(wriveted_user.id)
        try:
            stripe_customer.save()
        except Exception as e:
            # Can fail if e.g. current stripe api key doesn't have "rak_customer_write" permission
            logger.error(
                "Failed to update Stripe customer metadata with Wriveted user id",
                stripe_customer_id=stripe_customer_id,
                error=str(e),
            )

    if school is not None:
        _stamp_customer_school_metadata(stripe_customer, school)

    # ensure our db knows about the specified product
    stripe_price_id = stripe_subscription["items"]["data"][0]["price"]["id"]
    _sync_stripe_price_with_wriveted_product(session, stripe_price_id)

    # create or update a base subscription in our database
    wriveted_parent_id = (
        str(wriveted_user.id)
        if wriveted_user and wriveted_user.type == UserAccountType.PARENT
        else None
    )
    payment_status = event_data.get("payment_status")
    paid = payment_status in ("paid", "no_payment_required")
    stripe_status = getattr(stripe_subscription, "status", None)
    if not isinstance(stripe_status, str):
        stripe_status = None
    collection_method = stripe_subscription.get("collection_method")
    if not isinstance(collection_method, str):
        collection_method = None

    base_subscription_data = SubscriptionCreateIn(
        id=stripe_subscription_id,
        product_id=stripe_price_id,
        stripe_customer_id=stripe_subscription.customer,
        parent_id=wriveted_parent_id,
        school_id=str(school.wriveted_identifier) if school else None,
        expiration=stripe_subscription.current_period_end,
        stripe_status=stripe_status,
        collection_method=collection_method,
        paid_at=(datetime.utcnow() if paid else None),
    )
    logger.info(
        "Upserting subscription in our database",
        base_subscription_data=base_subscription_data,
        checkout_session_id=checkout_session_id,
    )
    subscription = subscription_repository.upsert(
        session, base_subscription_data, commit=False
    )
    logger.debug("Upserted subscription in our database", subscription=subscription)

    # Only a cleared payment marks the subscription active. "paid" = charged;
    # "no_payment_required" = 100%-off promo or trial (an intentional grant of
    # access). "unpaid" (async payment not yet cleared) leaves it inactive until
    # checkout.session.async_payment_succeeded arrives. Gating this (not just the
    # school state) keeps has_active_subscription / supporter flags honest.
    subscription.is_active = paid
    subscription.latest_checkout_session_id = checkout_session_id

    # fetch from db instead of stripe object in case we have a product name override
    product = product_repository.get_by_id(session, product_id=stripe_price_id)
    product_name = product.name if product else "Unknown Product"

    # Activate a paid or fully-comped school in the same transaction (before the
    # commit below).
    if school is not None:
        if paid:
            _activate_school_after_payment(session, school, stripe_customer_email)
            # A paying Stripe subscription supersedes any comped contribution
            # grant, so retire it to keep a single active subscription row.
            _retire_comp_grants(session, school.wriveted_identifier)
        else:
            logger.warning(
                "School checkout completed without cleared payment; leaving inactive",
                school=school.name,
                payment_status=payment_status,
            )

    create_event(
        session=session,
        title="Subscription started",
        description="Subscription created or updated",
        info={
            # "stripe_customer_id": stripe_customer_id,
            # "stripe_customer_name": stripe_customer.name,
            "stripe_customer_email": stripe_customer_email,
            "subscription_id": stripe_subscription_id,
            "stripe_product_id": stripe_price_id,
            "product_name": product_name,
        },
        account=wriveted_user,
        slack_channel=(
            None
            if checkout_session_id and "test" in checkout_session_id
            else EventSlackChannel.MEMBERSHIPS
        ),
        slack_extra={
            # "customer_name": stripe_customer.name,
            "customer_link": f"https://dashboard.stripe.com/customers/{stripe_customer_id}",
            # "subscription_link": f"https://dashboard.stripe.com/subscriptions/{stripe_subscription_id}",
            # "product_link": f"https://dashboard.stripe.com/products/{stripe_price_id}",
        },
        commit=False,
        enable_processing=True,  # This will automatically handle background processing via EventOutbox
        external_notifications=True,  # This will ensure Slack alerts are sent
    )

    # Processing automatically handled by unified workflow via enable_processing=True

    # Send subscription welcome email via EventOutbox for reliable delivery
    if wriveted_parent_id is not None and stripe_customer_email:
        logger.info("Sending subscription welcome email via EventOutbox")

        email_data = {
            "from_email": "orders@hueybooks.com",
            "from_name": "Huey Books",
            "to_emails": [stripe_customer_email],
            "subject": "Your Huey Books Membership",
            "template_id": "d-fa829ecc76fc4e37ab4819abb6e0d188",
            "template_data": {
                "name": stripe_customer.name,
                "checkout_session_id": checkout_session_id,
            },
        }

        # Send as TRANSACTIONAL email - critical business email with 5 retries
        send_email_reliable_sync(
            db=session,
            email_data=email_data,
            email_type=EmailType.TRANSACTIONAL,
            user_id=str(wriveted_user.id) if wriveted_user else None,
        )
    elif wriveted_parent_id is not None and not stripe_customer_email:
        logger.warning(
            "Skipping subscription welcome email - no customer email address available"
        )

    return subscription


def _activate_school_after_payment(session, school: School, customer_email):
    """Mark a paid school ACTIVE and email the contact a confirmation/receipt.

    Idempotent: Stripe redelivers events, so an already-active school is a
    no-op (and does not re-send the receipt).
    """
    school = lock_school_access_sync(session, school.wriveted_identifier)
    if school is None:
        return
    if school.state == SchoolState.ACTIVE:
        logger.info("School already active; ignoring duplicate", school=school.name)
        return

    school.state = SchoolState.ACTIVE
    session.add(school)

    onboarding = (school.info or {}).get("onboarding") or {}
    to_email = onboarding.get("contact_email") or customer_email
    if to_email:
        send_email_reliable_sync(
            db=session,
            email_data={
                "from_email": settings.BROADCAST_FROM_EMAIL,
                "from_name": "Huey Books",
                "to_emails": [to_email],
                "subject": f"{school.name} is live on Huey Books",
                "html_content": render_school_activated_html(
                    school.name,
                    onboarding.get("contact_name"),
                    settings.SCHOOL_ADMIN_URL,
                ),
            },
            email_type=EmailType.TRANSACTIONAL,
        )

    # No commit here: the caller commits (via create_event) so activation and the
    # receipt persist in the same transaction as the subscription.
    logger.info(
        "School activated after payment", school=school.name, emailed=bool(to_email)
    )


def _is_contribution_checkout(event_data: dict) -> bool:
    """Whether a completed checkout session is a one-off school contribution.

    Distinguished strictly by our own metadata marker, not by ``mode`` — a bare
    ``mode == "payment"`` check would swallow any future one-off checkout.
    """
    metadata = event_data.get("metadata") or {}
    return metadata.get("kind") == CONTRIBUTION_METADATA_KIND


def _resolve_school_from_client_reference(
    session, event_data: dict
) -> Optional[School]:
    """Resolve the school a contribution is scoped to from client_reference_id."""
    client_reference_id = event_data.get("client_reference_id")
    if client_reference_id in (None, "undefined", "null"):
        return None
    return school_repository.get_by_wriveted_id(
        session, wriveted_id=client_reference_id
    )


def _format_money(amount_total: Optional[int], currency: str) -> Optional[str]:
    """Format a Stripe minor-unit amount with its ISO currency code (no symbol)."""
    if amount_total is None:
        return None
    return f"{amount_total / 100:.2f} {currency.upper()}"


def _get_active_stripe_subscription(session, school: School) -> Optional[Subscription]:
    """Return the school's active auto-renewing Stripe subscription, if any.

    Queried directly rather than via ``school.subscription`` (a one-to-one
    relationship that is ambiguous if a school ever has both a comped grant row
    and a Stripe subscription row). Comped grants carry an empty
    ``stripe_customer_id``, so the ``!= ""`` filter excludes them.
    """
    return (
        session.execute(active_stripe_subscription_stmt(school.wriveted_identifier))
        .scalars()
        .first()
    )


def _get_active_comp_grant(session, school_id) -> Optional[Subscription]:
    """Return a school's active, unexpired comped grant (any source), if any.

    Used when a Stripe subscription is cancelled to decide whether the school
    should stay active — a live comp grant (contribution *or* invite trial) keeps
    it up."""
    now = datetime.utcnow()
    return (
        session.execute(
            select(Subscription)
            .where(
                Subscription.school_id == school_id,
                Subscription.is_active.is_(True),
                Subscription.info["source"].astext.in_(COMP_GRANT_SOURCES),
                Subscription.expiration > now,
            )
            .limit(1)
        )
        .scalars()
        .first()
    )


def _retire_comp_grants(session, school_id) -> None:
    """Deactivate a school's comped grants and any in-flight checkout reservation.

    Called when a school gains/uses a real auto-renewing Stripe subscription, so
    any superseded comp grant or checkout_pending reservation does not leave a
    second active ``subscriptions`` row — which would make ``School.subscription``
    (uselist=False) non-deterministic and the "supporter"/"paying" flags
    unreliable, and would keep the billing slot wrongly reserved.
    """
    if lock_school_access_sync(session, school_id) is None:
        return
    grants = (
        session.execute(
            select(Subscription).where(
                Subscription.school_id == school_id,
                Subscription.is_active.is_(True),
                Subscription.info["source"].astext.in_(RETIREABLE_SOURCES),
            )
        )
        .scalars()
        .all()
    )
    for grant in grants:
        grant.is_active = False
        logger.info(
            "Retired complimentary grant superseded by Stripe subscription",
            grant_id=grant.id,
        )


def _claim_contribution_session(session, checkout_session_id: str) -> bool:
    """Claim a checkout session id for processing; return True if we won the claim.

    Race-safe against Stripe webhook redelivery: the insert-on-conflict serialises
    concurrent deliveries on the session-id primary key, so only the first caller
    proceeds to activate the school / send emails.
    """
    result = session.execute(
        pg_insert(StripeContributionReceipt)
        .values(checkout_session_id=checkout_session_id)
        .on_conflict_do_nothing(index_elements=["checkout_session_id"])
    )
    return result.rowcount == 1


def _handle_contribution_checkout_completed(
    session, school: Optional[School], event_data: dict
) -> None:
    """Apply a completed one-off contribution toward a school's subscription.

    Contributions are pay-what-you-want (``amount_total`` varies per checkout).

    Crediting model:
    - **Active Stripe subscription** (auto-renewing): apply ``amount_total`` as a
      Stripe customer-balance credit on that subscription's customer, reducing the
      next renewal invoice.
    - **No active Stripe subscription**: the contribution buys a bounded paid
      grant whose length is proportional to the amount paid (see
      ``_contribution_grant_days``). A first-class comped Subscription is created
      (or, if one already exists, its expiry is extended by the newly-computed days
      — contributions stack), and the school is activated. The grant has no Stripe
      subscription behind it, so it does not auto-renew; it is lapsed to INACTIVE
      after expiry by the ``/maintenance/lapse-expired-schools`` sweep.

    Gated on payment_status == "paid" and idempotent on the checkout session id
    (Stripe redelivers webhook events).
    """
    payment_status = event_data.get("payment_status")
    if payment_status != "paid":
        logger.warning(
            "Contribution checkout completed without cleared payment; ignoring",
            payment_status=payment_status,
        )
        return

    checkout_session_id = event_data.get("id")
    if not checkout_session_id:
        logger.warning("Contribution checkout completed without an id; ignoring")
        return

    if school is None:
        logger.warning(
            "Contribution checkout could not be matched to a school; ignoring",
            checkout_session_id=checkout_session_id,
        )
        return

    if not _claim_contribution_session(session, checkout_session_id):
        logger.info(
            "Ignoring duplicate contribution event",
            checkout_session_id=checkout_session_id,
        )
        return

    school = lock_school_access_sync(session, school.wriveted_identifier)
    if school is None:
        return

    amount_total = event_data.get("amount_total")
    currency = (event_data.get("currency") or "aud").lower()
    payer_email = (event_data.get("customer_details") or {}).get(
        "email"
    ) or event_data.get("customer_email")
    amount_str = _format_money(amount_total, currency)

    active_stripe_subscription = _get_active_stripe_subscription(session, school)

    access_until: Optional[str] = None
    stripe_customer_id: Optional[str] = None
    if active_stripe_subscription is not None:
        stripe_customer_id = active_stripe_subscription.stripe_customer_id
        # A transient credit failure re-raises here (rolling back the claim); a
        # permanent one returns False.
        credited = _apply_customer_balance_credit(
            stripe_customer_id=stripe_customer_id,
            amount_total=amount_total,
            currency=currency,
            school_name=school.name,
            checkout_session_id=checkout_session_id,
        )
        # The live Stripe subscription supersedes any leftover comp grant row.
        _retire_comp_grants(session, school.wriveted_identifier)
        outcome = "credited" if credited else "received"
        crediting = "balance_credit" if credited else "credit_failed"
    else:
        outcome, expiration = _apply_or_extend_contribution_grant(
            session, school, amount_total
        )
        access_until = expiration.date().isoformat()
        crediting = "grant_extended" if outcome == "extended" else "school_activated"

    _record_contribution_receipt(
        session,
        checkout_session_id=checkout_session_id,
        school=school,
        amount_total=amount_total,
        currency=currency,
        crediting=crediting,
    )

    if payer_email:
        send_email_reliable_sync(
            db=session,
            email_data={
                "from_email": settings.BROADCAST_FROM_EMAIL,
                "from_name": "Huey Books",
                "to_emails": [payer_email],
                "subject": f"Thank you for contributing to {school.name}",
                "html_content": render_contribution_thankyou_html(
                    school.name, amount_str, outcome, access_until
                ),
            },
            email_type=EmailType.TRANSACTIONAL,
        )

    onboarding = (school.info or {}).get("onboarding") or {}
    school_contact_email = onboarding.get("contact_email")
    if school_contact_email and school_contact_email != payer_email:
        send_email_reliable_sync(
            db=session,
            email_data={
                "from_email": settings.BROADCAST_FROM_EMAIL,
                "from_name": "Huey Books",
                "to_emails": [school_contact_email],
                "subject": f"A supporter contributed to {school.name} on Huey Books",
                "html_content": render_school_contribution_notice_html(
                    school.name,
                    onboarding.get("contact_name"),
                    amount_str,
                    outcome,
                    access_until,
                ),
            },
            email_type=EmailType.TRANSACTIONAL,
        )

    create_event(
        session=session,
        title=CONTRIBUTION_EVENT_TITLE,
        description=f"Contribution received toward {school.name}",
        info={
            "checkout_session_id": checkout_session_id,
            "amount_total": amount_total,
            "currency": currency,
            "crediting": crediting,
            "outcome": outcome,
            "payer_email": payer_email,
            "access_until": access_until,
            "stripe_customer_id": stripe_customer_id,
        },
        school=school,
        slack_channel=(
            None if "test" in checkout_session_id else EventSlackChannel.MEMBERSHIPS
        ),
        slack_extra={"school_name": school.name, "amount": amount_str or ""},
        commit=False,
        enable_processing=True,
        external_notifications=True,
    )
    logger.info(
        "Processed school contribution",
        school=school.name,
        crediting=crediting,
        outcome=outcome,
        amount=amount_str,
        checkout_session_id=checkout_session_id,
    )


def _contribution_grant_days(amount_total: Optional[int]) -> int:
    """Days of access a pay-what-you-want contribution buys.

    Proportional to the amount paid against a configured monthly rate:
    ``round(amount / SCHOOL_CONTRIBUTION_MONTHLY_CENTS * 30)``, floored at 1 day
    so any accepted payment grants at least a day.
    """
    monthly_cents = settings.SCHOOL_CONTRIBUTION_MONTHLY_CENTS
    if not amount_total or monthly_cents <= 0:
        return 1
    # Clamp to [1, 10 years] so an absurd amount can't overflow the expiry
    # datetime and wedge the webhook in a retry loop.
    return max(1, min(round(amount_total / monthly_cents * 30), 3650))


def _apply_or_extend_contribution_grant(
    session, school: School, amount_total: Optional[int]
) -> tuple[str, datetime]:
    """Create or extend a school's comped contribution grant and activate it.

    Returns ``(outcome, expiration)`` where outcome is ``"activated"`` (a new or
    lapsed grant) or ``"extended"`` (an already-live grant whose expiry moved
    out). The granted period is proportional to the (pay-what-you-want) amount
    paid. Contributions stack: an unexpired grant extends by the newly-computed
    days from its current expiry, an expired/absent one from now.
    """
    now = datetime.utcnow()
    grant_days = _contribution_grant_days(amount_total)
    grant_id = f"{CONTRIBUTION_GRANT_SUBSCRIPTION_PREFIX}{school.wriveted_identifier}"

    school = lock_school_access_sync(session, school.wriveted_identifier)
    if school is None:
        raise ValueError("School disappeared while applying contribution grant")

    ensure_comp_product_sync(
        session, CONTRIBUTION_GRANT_PRODUCT_ID, CONTRIBUTION_GRANT_PRODUCT_NAME
    )

    # FOR UPDATE so concurrent contributions for the same school stack instead of
    # clobbering each other's expiry.
    grant = session.execute(
        select(Subscription).where(Subscription.id == grant_id).with_for_update()
    ).scalar_one_or_none()
    if grant is None:
        expiration = now + timedelta(days=grant_days)
        grant = Subscription(
            id=grant_id,
            school_id=school.wriveted_identifier,
            type=SubscriptionType.SCHOOL,
            stripe_customer_id="",
            is_active=True,
            expiration=expiration,
            product_id=CONTRIBUTION_GRANT_PRODUCT_ID,
            info={"source": CONTRIBUTION_GRANT_SOURCE},
        )
        session.add(grant)
        outcome = "activated"
    else:
        live = grant.is_active and grant.expiration and grant.expiration > now
        base = grant.expiration if live else now
        expiration = base + timedelta(days=grant_days)
        grant.is_active = True
        grant.expiration = expiration
        outcome = (
            "extended" if (live and school.state == SchoolState.ACTIVE) else "activated"
        )

    if school.state != SchoolState.ACTIVE:
        school.state = SchoolState.ACTIVE
        session.add(school)

    logger.info(
        "Applied contribution grant",
        school=school.name,
        grant_id=grant_id,
        outcome=outcome,
        expiration=str(expiration),
    )
    return outcome, expiration


def _record_contribution_receipt(
    session,
    *,
    checkout_session_id: str,
    school: School,
    amount_total: Optional[int],
    currency: str,
    crediting: str,
) -> None:
    """Fill in the details on the idempotency receipt claimed earlier."""
    receipt = session.get(StripeContributionReceipt, checkout_session_id)
    if receipt is not None:
        receipt.school_id = school.wriveted_identifier
        receipt.amount_total = amount_total
        receipt.currency = currency
        receipt.crediting = crediting


def _apply_customer_balance_credit(
    *,
    stripe_customer_id: str,
    amount_total: Optional[int],
    currency: str,
    school_name: str,
    checkout_session_id: str,
) -> bool:
    """Credit the contribution to the school's Stripe customer balance.

    A negative balance transaction reduces the customer's next renewal invoice.
    Returns True on success, False on a **permanent** failure (fail soft: the
    contribution can't be auto-credited and is recorded ``credit_failed`` for
    manual handling — e.g. a currency mismatch). A **potentially transient** Stripe
    error (rate limit, connection, API error) is **re-raised** so the caller
    aborts before committing the idempotency claim and Cloud Tasks retries; the
    ``idempotency_key`` makes that retry safe from double-crediting.
    """
    if not amount_total:
        logger.warning(
            "Contribution has no amount to credit; skipping balance credit",
            checkout_session_id=checkout_session_id,
        )
        return False

    # Retrieving the customer can fail transiently; let that re-raise to retry.
    customer = StripeCustomer.retrieve(stripe_customer_id)
    customer_currency = (customer.get("currency") or "").lower()
    if customer_currency and customer_currency != currency:
        # Permanent: a balance transaction must match the customer's currency.
        logger.error(
            "Contribution currency does not match customer balance currency; "
            "skipping balance credit for manual handling",
            stripe_customer_id=stripe_customer_id,
            contribution_currency=currency,
            customer_currency=customer_currency,
            checkout_session_id=checkout_session_id,
        )
        return False

    try:
        StripeCustomer.create_balance_transaction(
            stripe_customer_id,
            amount=-amount_total,
            currency=currency,
            description=(
                f"Contribution toward {school_name} (checkout {checkout_session_id})"
            ),
            idempotency_key=f"contribution-{checkout_session_id}",
        )
    except stripe.error.InvalidRequestError as e:
        # Permanent client error (bad params, e.g. currency mismatch that slipped
        # past the check): retrying won't help, so fail soft for manual handling.
        logger.error(
            "Permanent Stripe error applying contribution credit; recording as failed",
            stripe_customer_id=stripe_customer_id,
            checkout_session_id=checkout_session_id,
            error=str(e),
        )
        return False
    except Exception as e:
        # Potentially transient (rate limit, connection, API error, or unexpected):
        # re-raise so the claim is not committed and the task retries.
        logger.warning(
            "Transient error applying contribution credit; re-raising to retry",
            stripe_customer_id=stripe_customer_id,
            checkout_session_id=checkout_session_id,
            error=str(e),
        )
        raise

    logger.info(
        "Applied contribution as customer balance credit",
        stripe_customer_id=stripe_customer_id,
        amount=amount_total,
        currency=currency,
    )
    return True


def _handle_invoice_upcoming(session, event_data):
    """Remind an active school's contact that their subscription renews soon."""
    stripe_subscription_id = _invoice_subscription_id(event_data)
    if not stripe_subscription_id:
        return
    subscription = subscription_repository.get_by_id(
        session, subscription_id=stripe_subscription_id
    )
    if subscription is None or not subscription.school_id:
        return
    school = school_repository.get_by_wriveted_id(session, str(subscription.school_id))
    if school is None or school.state != SchoolState.ACTIVE:
        return

    onboarding = (school.info or {}).get("onboarding") or {}
    to_email = onboarding.get("contact_email")
    if not to_email:
        return

    amount = event_data.get("amount_due")
    currency = (event_data.get("currency") or "aud").upper()
    amount_str = (
        f"${(amount or 0) / 100:.2f} {currency}" if amount is not None else None
    )
    renews_at = event_data.get("next_payment_attempt") or event_data.get("period_end")
    renewal_date = (
        datetime.utcfromtimestamp(renews_at).date().isoformat() if renews_at else None
    )

    send_email_reliable_sync(
        db=session,
        email_data={
            "from_email": settings.BROADCAST_FROM_EMAIL,
            "from_name": "Huey Books",
            "to_emails": [to_email],
            "subject": f"{school.name} — your Huey Books subscription renews soon",
            "html_content": render_school_renewal_reminder_html(
                school.name, onboarding.get("contact_name"), amount_str, renewal_date
            ),
        },
        email_type=EmailType.TRANSACTIONAL,
    )
    logger.info("Sent school renewal reminder", school=school.name)


def _is_unpaid_invoice_subscription(event_data: dict) -> bool:
    """True for a ``send_invoice`` subscription whose latest invoice is not paid.

    A net-terms (``collection_method='send_invoice'``) subscription is reported
    ``active`` by Stripe the moment it is created — before the invoice is paid.
    Retiring the ``invoice_pending`` comp grant on that pre-payment event would
    defeat the never-paid lapse backstop, so callers skip the retire for these:
    only ``invoice.paid`` should retire the grant. Unknown/unretrievable invoice
    status is treated as unpaid (conservative — do not retire early).
    """
    if event_data.get("collection_method") != "send_invoice":
        return False

    latest_invoice = event_data.get("latest_invoice")
    status = None
    if isinstance(latest_invoice, dict):
        status = latest_invoice.get("status")
    elif isinstance(latest_invoice, str) and latest_invoice:
        try:
            status = stripe.Invoice.retrieve(latest_invoice).get("status")
        except Exception as e:
            logger.warning(
                "Could not retrieve latest invoice to check paid status",
                latest_invoice=latest_invoice,
                error=str(e),
            )
            status = None
    return status != "paid"


def _handle_subscription_created(
    session, wriveted_user: Optional[User], school: School | None, event_data: dict
):
    stripe_subscription_id = event_data.get("id")
    assert event_data.get("object") == "subscription"
    assert stripe_subscription_id is not None, "Subscription ID is required"

    stripe_subscription_status = event_data["status"]
    stripe_subscription_expiry = event_data["current_period_end"]

    # ensure our db knows about the specified product
    stripe_price_id = event_data["items"]["data"][0]["price"]["id"]
    _sync_stripe_price_with_wriveted_product(session, stripe_price_id)

    # If user is missing, look to see if the Stripe Customer's metadata includes `wriveted_id`
    if wriveted_user is None:
        stripe_customer = _get_stripe_customer_from_stripe_object(
            event_data, "subscription"
        )

        # check customer metadata for a wriveted user id
        # (this is stored upon the first successful checkout)
        if user_id := stripe_customer["metadata"].get("wriveted_id"):
            wriveted_user = crud.user.get(session, user_id)
            logger.info(
                "Found wriveted user id in Stripe Customer metadata", user=wriveted_user
            )

    wriveted_parent_id = (
        str(wriveted_user.id)
        if wriveted_user and wriveted_user.type == UserAccountType.PARENT
        else None
    )
    is_school_subscription = school is not None
    subscription_data = SubscriptionCreateIn(
        id=stripe_subscription_id,
        type=SubscriptionType.FAMILY if wriveted_parent_id else SubscriptionType.SCHOOL,
        # A school subscription is not an entitlement until Checkout or
        # invoice.paid supplies payment evidence. Family subscriptions retain
        # their legacy status-driven behavior.
        is_active=(
            not is_school_subscription
            and stripe_subscription_status in {"active", "past_due"}
        ),
        product_id=stripe_price_id,
        stripe_customer_id=str(event_data.get("customer"))
        if event_data.get("customer")
        else "",
        parent_id=wriveted_parent_id,
        school_id=str(school.wriveted_identifier) if school else None,
        expiration=stripe_subscription_expiry,
        stripe_status=stripe_subscription_status,
        collection_method=event_data.get("collection_method"),
    )

    logger.debug(
        "Creating subscription in our database", subscription_data=subscription_data
    )
    subscription, created = subscription_repository.get_or_create(
        session, subscription_data, commit=False
    )
    if created:
        logger.info("Created a new subscription", subscription=subscription)


def _handle_subscription_updated(
    session, wriveted_user: Optional[User], school: School | None, event_data: dict
) -> Optional[Subscription]:
    stripe_subscription_id = event_data.get("id")
    assert event_data.get("object") == "subscription"

    stripe_subscription_status = event_data["status"]

    # ensure our db knows about the specified product
    stripe_price_id = event_data["items"]["data"][0]["price"]["id"]
    product = _sync_stripe_price_with_wriveted_product(session, stripe_price_id)

    # If user is missing, look to see if the Stripe Customer's metadata includes `wriveted_id`
    if wriveted_user is None:
        stripe_customer = _get_stripe_customer_from_stripe_object(
            event_data, "subscription"
        )

        # check customer metadata for a wriveted user id
        # (this is stored upon the first successful checkout)
        if user_id := stripe_customer["metadata"].get("wriveted_id"):
            wriveted_user = crud.user.get(session, user_id)
            logger.info(
                "Found wriveted user id in Stripe Customer metadata", user=wriveted_user
            )

    subscription = subscription_repository.get_by_id(
        session, subscription_id=stripe_subscription_id
    )
    if not subscription:
        logger.warning(
            "Ignoring subscription update event for missing subscription",
            subscription=subscription,
        )
        return

    # populate the subscription in our database with the latest information
    subscription.product_id = stripe_price_id
    subscription.is_active = stripe_subscription_status in {"active", "past_due"} and (
        subscription.type != SubscriptionType.SCHOOL or subscription.paid_at is not None
    )
    subscription.stripe_status = stripe_subscription_status
    subscription.collection_method = event_data.get("collection_method")
    if subscription.type != SubscriptionType.SCHOOL or subscription.paid_at is None:
        subscription.expiration = datetime.utcfromtimestamp(
            event_data["current_period_end"]
        )

    if (
        wriveted_user
        and subscription.type == SubscriptionType.FAMILY
        and subscription.parent_id is None
    ):
        # we have a wriveted user, but no wriveted id on the subscription
        logger.info("Updating family subscription with Wriveted user id")
        subscription.parent_id = wriveted_user.id

    if (
        school
        and subscription.type == SubscriptionType.SCHOOL
        and subscription.school_id is None
    ):
        # we have a school, but no school id on the subscription
        logger.info("Updating school subscription with school id")
        subscription.school_id = school.wriveted_identifier

    if (
        school is not None
        and subscription.type == SubscriptionType.SCHOOL
        and subscription.is_active
        and subscription.stripe_customer_id
        and not _is_unpaid_invoice_subscription(event_data)
    ):
        school = lock_school_access_sync(session, school.wriveted_identifier)
        if school is not None:
            _retire_comp_grants(session, school.wriveted_identifier)

    # Use unified event workflow instead of direct crud.event.create
    create_event(
        session=session,
        title="Subscription updated",
        description="Subscription updated on Stripe",
        info={
            "product": product.name if product else "Unknown Product",
            "stripe_subscription_id": stripe_subscription_id,
            "product_id": stripe_price_id,
            "status": stripe_subscription_status,
        },
        school=school,
        account=wriveted_user,
        commit=False,
        enable_processing=False,  # No processing needed for updates
        external_notifications=False,  # Internal accounting event
    )

    return subscription


def _handle_subscription_cancelled(
    session, wriveted_user: Optional[User], school: School | None, event_data: dict
):
    stripe_subscription_id = event_data.get("id")
    subscription = subscription_repository.get_by_id(
        session, subscription_id=stripe_subscription_id
    )

    if subscription is not None:
        logger.info("Marking subscription as inactive", subscription=subscription)
        product = subscription.product
        subscription.is_active = False
        subscription.stripe_status = event_data.get("status") or "canceled"
        if "ended_at" in event_data and event_data["ended_at"] is not None:
            subscription.expiration = datetime.utcfromtimestamp(event_data["ended_at"])

        # Deactivate the school this subscription paid for. The school is not in
        # the subscription.deleted payload (client_reference_id is only on
        # checkout sessions), so resolve it from our subscription record.
        if school is None and subscription.school_id:
            school = school_repository.get_by_wriveted_id(
                session, str(subscription.school_id)
            )
        if school is not None:
            school = lock_school_access_sync(session, school.wriveted_identifier)
        if school is not None:
            recompute_school_access_sync(session, school)

        # Use unified event workflow instead of direct crud.event.create
        create_event(
            session=session,
            title="Subscription cancelled",
            description=f"User cancelled their subscription to {product.name if product else 'Unknown Product'}",
            info={
                "stripe_subscription_id": stripe_subscription_id,
                "product_id": product.id if product else "unknown",
                "product_name": product.name if product else "Unknown Product",
                "cancellation_details": event_data.get("cancellation_reason", {}),
            },
            school=school,
            account=wriveted_user,
            slack_channel=EventSlackChannel.MEMBERSHIPS,  # Important business event - notify team
            commit=False,
            enable_processing=False,  # No processing needed for cancellations
            external_notifications=True,  # Team should be notified of cancellations
        )
    else:
        logger.info(
            "Ignoring subscription cancelled event for unknown subscription (likely already removed)",
            stripe_subscription_id=stripe_subscription_id,
        )


def _sync_stripe_price_with_wriveted_product(
    session, stripe_price_id: str
) -> Optional[Product]:
    # Note multiple stripe events will all occur at essentially the same time.
    # We upsert into product table to avoid conflict

    logger.debug("Syncing Stripe price with Wriveted product")
    wriveted_product = product_repository.get_by_id(session, product_id=stripe_price_id)
    if not wriveted_product:
        logger.info("Creating new product in db")
        stripe_price = StripePrice.retrieve(stripe_price_id)
        stripe_product = StripeProduct.retrieve(stripe_price.product)

        product_repository.upsert(
            session,
            ProductCreateIn(id=stripe_price_id, name=stripe_product.name),
            commit=False,
        )
        wriveted_product = product_repository.get_by_id(
            session, product_id=stripe_price_id
        )

        logger.info(
            "Created new product in db",
            product_id=stripe_price_id,
            product_name=wriveted_product.name
            if wriveted_product
            else "Unknown Product",
        )
    else:
        logger.debug(
            "Product already exists in db",
            product_id=stripe_price_id,
            product_name=wriveted_product.name
            if wriveted_product
            else "Unknown Product",
        )
    return wriveted_product

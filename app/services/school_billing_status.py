from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.school import School, SchoolState
from app.models.school_billing import (
    OPEN_COLLECTIBLE_ATTEMPT_STATUSES,
    SchoolBillingAccount,
    SchoolBillingAttempt,
    SchoolBillingAttemptStatus,
)
from app.models.subscription import Subscription
from app.schemas.school_billing import (
    PaidSchoolSubscription,
    SchoolBillingAttemptBrief,
    SchoolBillingCapabilities,
    SchoolBillingEntitlement,
    SchoolBillingOffer,
    SchoolBillingStatus,
)
from app.services.school_access import (
    COMP_GRANT_SOURCES,
    INVOICE_PENDING_GRANT_SOURCE,
    subscription_blocks_new_billing,
)

PAID_ENTITLEMENT_SOURCE = "paid_subscription"
LEGACY_ENTITLEMENT_SOURCE = "legacy_subscription"
INVOICE_PREPARATION_FAILURE_MESSAGE = (
    "Stripe could not finish preparing this invoice; our team has been notified"
)


def select_school_price_id(school: School) -> str:
    settings = get_settings()
    if not settings.STRIPE_SCHOOL_PRICE_IDS:
        raise ValueError("STRIPE_SCHOOL_PRICE_IDS is not configured")
    return (
        settings.STRIPE_SCHOOL_PRICE_IDS_BY_COUNTRY.get(school.country_code)
        or settings.STRIPE_SCHOOL_PRICE_IDS[0]
    )


async def resolve_school_billing_status(
    session: AsyncSession, school: School
) -> SchoolBillingStatus:
    latest_attempt = (
        (
            await session.execute(
                select(SchoolBillingAttempt)
                .where(SchoolBillingAttempt.school_id == school.wriveted_identifier)
                .order_by(SchoolBillingAttempt.created_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )

    subscriptions = list(
        (
            await session.execute(
                select(Subscription).where(
                    Subscription.school_id == school.wriveted_identifier
                )
            )
        ).scalars()
    )
    has_billing_account = (
        await session.get(SchoolBillingAccount, school.wriveted_identifier) is not None
    )
    return _build_school_billing_status(
        school, latest_attempt, subscriptions, has_billing_account
    )


def resolve_school_billing_status_sync(
    session: Session, school: School
) -> SchoolBillingStatus:
    latest_attempt = (
        session.execute(
            select(SchoolBillingAttempt)
            .where(SchoolBillingAttempt.school_id == school.wriveted_identifier)
            .order_by(SchoolBillingAttempt.created_at.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    subscriptions = list(
        session.execute(
            select(Subscription).where(
                Subscription.school_id == school.wriveted_identifier
            )
        ).scalars()
    )
    has_billing_account = (
        session.get(SchoolBillingAccount, school.wriveted_identifier) is not None
    )
    return _build_school_billing_status(
        school, latest_attempt, subscriptions, has_billing_account
    )


def _build_school_billing_status(
    school: School,
    latest_attempt: SchoolBillingAttempt | None,
    subscriptions: list[Subscription],
    has_billing_account: bool,
) -> SchoolBillingStatus:
    now = datetime.utcnow()
    entitlement, paid_subscription = _resolve_entitlement(subscriptions, now)
    has_legacy_blocking_obligation = any(
        subscription_blocks_new_billing(subscription, now=now)
        for subscription in subscriptions
    )

    latest_attempt_is_stale = (
        latest_attempt is not None
        and latest_attempt.status in OPEN_COLLECTIBLE_ATTEMPT_STATUSES
        and latest_attempt.expires_at is not None
        and latest_attempt.expires_at <= now
    )
    current_attempt = (
        SchoolBillingAttemptBrief(
            id=latest_attempt.id,
            method=latest_attempt.method,
            status=(
                SchoolBillingAttemptStatus.EXPIRED
                if latest_attempt_is_stale
                and latest_attempt.status != SchoolBillingAttemptStatus.CREATING
                else latest_attempt.status
            ),
            checkout_url=latest_attempt.checkout_url,
            hosted_invoice_url=latest_attempt.hosted_invoice_url,
            billing_email=latest_attempt.billing_email,
            billing_name=latest_attempt.billing_name,
            purchase_order_number=latest_attempt.purchase_order_number,
            expires_at=latest_attempt.expires_at,
            failure_reason=(
                INVOICE_PREPARATION_FAILURE_MESSAGE
                if latest_attempt.failure_reason
                else None
            ),
        )
        if latest_attempt is not None
        else None
    )
    paid_subscription_dto = (
        PaidSchoolSubscription(
            id=paid_subscription.id,
            stripe_status=paid_subscription.stripe_status,
            expires_at=paid_subscription.expiration,
        )
        if paid_subscription is not None
        else None
    )
    has_open_attempt = (
        latest_attempt is not None
        and latest_attempt.status in OPEN_COLLECTIBLE_ATTEMPT_STATUSES
        and (
            latest_attempt.status == SchoolBillingAttemptStatus.CREATING
            or not latest_attempt_is_stale
        )
    )
    can_start = (
        paid_subscription is None
        and not has_open_attempt
        and not has_legacy_blocking_obligation
    )
    if paid_subscription is not None:
        blocking_reason = "paid_subscription"
    elif (
        latest_attempt is not None
        and latest_attempt.status == SchoolBillingAttemptStatus.CREATING
        and latest_attempt_is_stale
    ):
        blocking_reason = "attempt_requires_review"
    elif has_open_attempt:
        blocking_reason = "attempt_in_progress"
    elif has_legacy_blocking_obligation:
        blocking_reason = "legacy_obligation"
    else:
        blocking_reason = None
    settings = get_settings()
    return SchoolBillingStatus(
        entitlement=entitlement,
        current_attempt=current_attempt,
        paid_subscription=paid_subscription_dto,
        capabilities=SchoolBillingCapabilities(
            card=can_start,
            invoice=can_start,
            manage=paid_subscription is not None and has_billing_account,
            blocking_reason=blocking_reason,
        ),
        invoice_first=school.country_code in settings.INVOICE_FIRST_COUNTRY_CODES,
        offer=SchoolBillingOffer(
            price_id=select_school_price_id(school),
            unit_amount=settings.STRIPE_SCHOOL_UNIT_AMOUNT_BY_COUNTRY.get(
                school.country_code, settings.STRIPE_SCHOOL_DEFAULT_UNIT_AMOUNT
            ),
            currency=settings.STRIPE_SCHOOL_CURRENCY,
            interval=settings.STRIPE_SCHOOL_BILLING_INTERVAL,
            interval_count=settings.STRIPE_SCHOOL_BILLING_INTERVAL_COUNT,
            invoice_days_until_due=settings.INVOICE_DAYS_UNTIL_DUE,
        ),
    )


def _resolve_entitlement(
    subscriptions: list[Subscription], now: datetime
) -> tuple[SchoolBillingEntitlement, Subscription | None]:
    paid_subscriptions = [
        subscription
        for subscription in subscriptions
        if subscription.is_active
        and subscription.stripe_customer_id
        and subscription.paid_at is not None
        and subscription.expiration > now
    ]
    paid_subscription = max(
        paid_subscriptions,
        key=lambda subscription: subscription.expiration,
        default=None,
    )

    live_grants = [
        subscription
        for subscription in subscriptions
        if subscription.is_active
        and subscription.expiration > now
        and (subscription.info or {}).get("source") in COMP_GRANT_SOURCES
    ]
    invoice_pending = max(
        (
            grant
            for grant in live_grants
            if (grant.info or {}).get("source") == INVOICE_PENDING_GRANT_SOURCE
        ),
        key=lambda grant: grant.expiration,
        default=None,
    )
    ordinary_grant = max(
        (
            grant
            for grant in live_grants
            if (grant.info or {}).get("source") != INVOICE_PENDING_GRANT_SOURCE
        ),
        key=lambda grant: grant.expiration,
        default=None,
    )
    legacy_subscription = max(
        (
            subscription
            for subscription in subscriptions
            if subscription.is_active
            and subscription.stripe_customer_id
            and subscription.paid_at is None
            and subscription.latest_checkout_session_id is not None
            and subscription.expiration > now
        ),
        key=lambda subscription: subscription.expiration,
        default=None,
    )
    if paid_subscription is not None:
        entitlement = SchoolBillingEntitlement(
            active=True,
            source=PAID_ENTITLEMENT_SOURCE,
            expires_at=paid_subscription.expiration,
        )
    elif invoice_pending is not None:
        entitlement = SchoolBillingEntitlement(
            active=True,
            source=INVOICE_PENDING_GRANT_SOURCE,
            expires_at=invoice_pending.expiration,
        )
    elif ordinary_grant is not None:
        entitlement = SchoolBillingEntitlement(
            active=True,
            source=(ordinary_grant.info or {}).get("source"),
            expires_at=ordinary_grant.expiration,
        )
    elif legacy_subscription is not None:
        entitlement = SchoolBillingEntitlement(
            active=True,
            source=LEGACY_ENTITLEMENT_SOURCE,
            expires_at=legacy_subscription.expiration,
        )
    else:
        entitlement = SchoolBillingEntitlement(active=False)
    return entitlement, paid_subscription


async def recompute_school_access(session: AsyncSession, school: School) -> bool:
    subscriptions = list(
        (
            await session.execute(
                select(Subscription).where(
                    Subscription.school_id == school.wriveted_identifier
                )
            )
        ).scalars()
    )
    entitlement, _ = _resolve_entitlement(subscriptions, datetime.utcnow())
    desired_state = SchoolState.ACTIVE if entitlement.active else SchoolState.INACTIVE
    changed = school.state != desired_state
    if changed:
        school.state = desired_state
        session.add(school)
        await session.flush()
    return changed


def recompute_school_access_sync(session: Session, school: School) -> bool:
    subscriptions = list(
        session.execute(
            select(Subscription).where(
                Subscription.school_id == school.wriveted_identifier
            )
        ).scalars()
    )
    entitlement, _ = _resolve_entitlement(subscriptions, datetime.utcnow())
    desired_state = SchoolState.ACTIVE if entitlement.active else SchoolState.INACTIVE
    changed = school.state != desired_state
    if changed:
        school.state = desired_state
        session.add(school)
        session.flush()
    return changed

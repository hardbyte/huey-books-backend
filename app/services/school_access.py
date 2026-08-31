"""Comped school access grants shared across sources (contributions, invites).

A comped grant is a synthetic ``Subscription`` row with an empty
``stripe_customer_id`` (so it never counts as "paying"), ``is_active=True`` and a
future ``expiration``. Its presence flips ``school.state`` to ACTIVE; its expiry
is enforced by the lapse sweep (``/maintenance/lapse-expired-schools``), which
covers every source in ``COMP_GRANT_SOURCES``.

The pay-what-you-want contribution grant lives in ``stripe_events`` (sync, Stripe
webhook path). This module adds the **invite** grant (async, used by the accept
flow) and the source registry both share.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from structlog import get_logger

from app.models.event import Event
from app.models.product import Product
from app.models.school import School, SchoolState
from app.models.service_account import ServiceAccount
from app.models.subscription import Subscription, SubscriptionType
from app.models.user import User
from app.repositories.event_repository import event_repository

logger = get_logger()

# Mirrors stripe_events.CONTRIBUTION_GRANT_SOURCE (kept as a literal here to keep
# this a leaf module — stripe_events/internal API import COMP_GRANT_SOURCES from
# here, so importing it from stripe_events would be circular).
CONTRIBUTION_GRANT_SOURCE = "contribution_grant"
INVITE_GRANT_SOURCE = "invite_grant"
STAFF_COMP_GRANT_SOURCE = "staff_comp"
# Access held during net-terms while an invoice is unpaid. Paying the invoice
# retires it (via _retire_comp_grants); an invoice that is never paid lapses it
# via the lapse sweep, dropping the school to INACTIVE.
INVOICE_PENDING_GRANT_SOURCE = "invoice_pending"
# Every comped (non-paying) grant source that grants access (flips the school
# ACTIVE). The lapse sweep and Stripe-conversion retirement operate over this
# whole set, not just contributions.
COMP_GRANT_SOURCES = frozenset(
    {
        CONTRIBUTION_GRANT_SOURCE,
        INVITE_GRANT_SOURCE,
        STAFF_COMP_GRANT_SOURCE,
        INVOICE_PENDING_GRANT_SOURCE,
    }
)

# Comp sources that must NOT block a new billing attempt: a school on a staff,
# invite, or contribution comp must be able to convert to paid. (invoice_pending
# is deliberately excluded — it represents an outstanding invoice obligation.)
NON_BLOCKING_COMP_SOURCES = frozenset(
    {
        CONTRIBUTION_GRANT_SOURCE,
        INVITE_GRANT_SOURCE,
        STAFF_COMP_GRANT_SOURCE,
    }
)

RETIREABLE_SOURCES = COMP_GRANT_SOURCES

INVITE_GRANT_PRODUCT_ID = "comp_school_invite"
INVITE_GRANT_PRODUCT_NAME = "School invite (comped)"
INVITE_GRANT_SUBSCRIPTION_PREFIX = "comp_invite_"

STAFF_COMP_PRODUCT_ID = "comp_staff_grant"
STAFF_COMP_PRODUCT_NAME = "Staff complimentary grant"
STAFF_COMP_SUBSCRIPTION_PREFIX = "comp_staff_"
SCHOOL_COMP_GRANTED_EVENT_TITLE = "School complimentary access granted"

INVOICE_PENDING_PRODUCT_ID = "comp_invoice_pending"
INVOICE_PENDING_PRODUCT_NAME = "Invoice pending (net terms)"
INVOICE_PENDING_SUBSCRIPTION_PREFIX = "comp_invoice_pending_"


def active_stripe_subscription_stmt(school_id):
    """SELECT for a school's current paid Stripe entitlement.

    The shared predicate for "the school has a real Stripe subscription":
    ``is_active`` and a non-empty ``stripe_customer_id`` (comped grants carry an
    empty customer id, so ``!= ""`` excludes them). Hoisted here so the webhook
    handlers, the billing-portal endpoint and the invoice-subscription guard all
    apply one definition.
    """
    return (
        select(Subscription)
        .where(
            Subscription.school_id == school_id,
            Subscription.is_active.is_(True),
            Subscription.stripe_customer_id != "",
            Subscription.paid_at.is_not(None),
            Subscription.expiration > datetime.utcnow(),
        )
        .limit(1)
    )


async def get_active_stripe_subscription_async(
    session: AsyncSession, school_id
) -> Subscription | None:
    return (
        await session.execute(active_stripe_subscription_stmt(school_id))
    ).scalar_one_or_none()


async def has_blocking_billing_obligation_async(
    session: AsyncSession, school_id
) -> bool:
    """Whether an existing obligation must block a NEW billing attempt.

    Blocks on a real Stripe obligation (a card/invoice Stripe subscription, or an
    in-flight ``invoice_pending`` / ``checkout_pending`` row); does NOT block on a
    staff, invite, or contribution comp — a comped school must be free to convert
    to paid. Equivalent to: any active row whose source is not a non-blocking comp
    (a real paying Stripe sub has no ``source``, so it blocks; ``coalesce`` keeps
    that NULL out of the exclusion set).

    Shared by both billing entry points (card checkout and invoice subscription)
    so neither can start a second collectible obligation while one is already live.
    """
    now = datetime.utcnow()
    return (
        await session.execute(
            select(Subscription.id)
            .where(
                Subscription.school_id == school_id,
                Subscription.is_active.is_(True),
                func.coalesce(Subscription.info["source"].astext, "").notin_(
                    NON_BLOCKING_COMP_SOURCES
                ),
                or_(
                    Subscription.stripe_customer_id != "",
                    Subscription.expiration > now,
                ),
            )
            .limit(1)
        )
    ).first() is not None


def subscription_blocks_new_billing(
    subscription: Subscription, *, now: datetime
) -> bool:
    """Pure counterpart to ``has_blocking_billing_obligation_async``."""
    if not subscription.is_active:
        return False
    if (subscription.info or {}).get("source") in NON_BLOCKING_COMP_SOURCES:
        return False
    return bool(subscription.stripe_customer_id) or subscription.expiration > now


def _active_access_grant_sync(session: Session, school_id) -> Subscription | None:
    """A school's live access grant (any ``COMP_GRANT_SOURCES`` row, unexpired)."""
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


def deactivate_school_on_non_payment_sync(session: Session, school: School) -> bool:
    """Retire the invoice_pending grant and drop the school on terminal non-payment.

    Belt-and-suspenders for voided / uncollectible invoices so never-paid access
    does not hinge on Stripe's "cancel overdue subscription" setting: for a school
    whose access rests on an unpaid invoice (no real *paid* Stripe subscription),
    retire its ``invoice_pending`` grant and set it INACTIVE unless another live
    access grant still covers it. A paying school (a live Stripe sub exists) is
    left untouched. Returns whether the school was set INACTIVE.
    """
    locked = lock_school_access_sync(session, school.wriveted_identifier)
    if locked is None:
        return False
    school = locked

    grant = session.get(
        Subscription, invoice_pending_grant_id(school.wriveted_identifier)
    )
    if grant is not None and grant.is_active:
        grant.is_active = False
        session.flush()
        logger.info(
            "Retired invoice_pending grant on uncollectible invoice",
            grant_id=grant.id,
        )

    from app.services.school_billing_status import recompute_school_access_sync

    return recompute_school_access_sync(session, school)


async def known_stripe_customer_id_async(
    session: AsyncSession, school_id
) -> str | None:
    """The Stripe customer id previously associated with this school, if any.

    Reused so a second checkout / invoice attaches to the same Stripe Customer
    rather than creating a duplicate. Prefers a live subscription's customer but
    falls back to any non-empty one on record.
    """
    row = (
        await session.execute(
            select(Subscription.stripe_customer_id)
            .where(
                Subscription.school_id == school_id,
                Subscription.stripe_customer_id != "",
            )
            .order_by(Subscription.is_active.desc(), Subscription.updated_at.desc())
            .limit(1)
        )
    ).first()
    return row[0] if row else None


def invoice_pending_grant_id(school_wriveted_id) -> str:
    return f"{INVOICE_PENDING_SUBSCRIPTION_PREFIX}{school_wriveted_id}"


StaffCompOutcome = Literal["granted", "extended", "unchanged"]


class SchoolAccessError(Exception):
    """Base class for school access business errors."""


class SchoolNotFoundError(SchoolAccessError):
    """The requested school does not exist."""


class ActivePaidSubscriptionError(SchoolAccessError):
    """A paid subscription already controls the school's access."""


@dataclass(frozen=True)
class StaffCompResult:
    outcome: StaffCompOutcome
    state: SchoolState
    access_until: datetime
    idempotent_replay: bool = False


def invite_grant_id(school_wriveted_id) -> str:
    return f"{INVITE_GRANT_SUBSCRIPTION_PREFIX}{school_wriveted_id}"


def staff_comp_id(school_wriveted_id) -> str:
    return f"{STAFF_COMP_SUBSCRIPTION_PREFIX}{school_wriveted_id}"


async def lock_school_access_async(
    session: AsyncSession, school_id: UUID
) -> School | None:
    return (
        await session.execute(
            select(School)
            .where(School.wriveted_identifier == school_id)
            .with_for_update()
        )
    ).scalar_one_or_none()


def lock_school_access_sync(session: Session, school_id: UUID) -> School | None:
    return session.execute(
        select(School).where(School.wriveted_identifier == school_id).with_for_update()
    ).scalar_one_or_none()


async def ensure_comp_product_async(
    session: AsyncSession, product_id: str, name: str
) -> None:
    await session.execute(
        pg_insert(Product)
        .values(id=product_id, name=name)
        .on_conflict_do_nothing(index_elements=[Product.id])
    )


def ensure_comp_product_sync(session: Session, product_id: str, name: str) -> None:
    session.execute(
        pg_insert(Product)
        .values(id=product_id, name=name)
        .on_conflict_do_nothing(index_elements=[Product.id])
    )


def _staff_comp_result_from_event(event: Event) -> StaffCompResult:
    info = event.info or {}
    return StaffCompResult(
        outcome=info["outcome"],
        state=SchoolState(info["state"]),
        access_until=datetime.fromisoformat(info["access_until"]),
        idempotent_replay=True,
    )


async def grant_staff_comp(
    session: AsyncSession,
    school_id: UUID,
    *,
    days: int,
    account: User | ServiceAccount | None,
    idempotency_key: str,
    reason: str | None,
    campaign_id: str | None,
) -> StaffCompResult:
    """Give a school a staff-authorised complimentary access window.

    The school row serialises every access transition. The event is both the audit
    record and the idempotency record, committed atomically with the subscription.
    """
    now = datetime.utcnow()
    school = await lock_school_access_async(session, school_id)
    if school is None:
        raise SchoolNotFoundError

    prior_event = (
        await session.execute(
            select(Event)
            .where(
                Event.school_id == school.id,
                Event.title == SCHOOL_COMP_GRANTED_EVENT_TITLE,
                Event.info["idempotency_key"].astext == idempotency_key,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if prior_event is not None:
        return _staff_comp_result_from_event(prior_event)

    has_paying_subscription = (
        await session.execute(
            select(Subscription.id)
            .where(
                Subscription.school_id == school_id,
                Subscription.is_active.is_(True),
                Subscription.stripe_customer_id != "",
            )
            .limit(1)
        )
    ).first()
    if has_paying_subscription is not None:
        raise ActivePaidSubscriptionError

    grant_id = staff_comp_id(school_id)
    target = now + timedelta(days=days)

    await ensure_comp_product_async(
        session, STAFF_COMP_PRODUCT_ID, STAFF_COMP_PRODUCT_NAME
    )

    existing = (
        await session.execute(
            select(Subscription).where(Subscription.id == grant_id).with_for_update()
        )
    ).scalar_one_or_none()
    previous_expiration = existing.expiration if existing is not None else None
    if existing is None:
        expiration = target
        outcome: StaffCompOutcome = "granted"
        session.add(
            Subscription(
                id=grant_id,
                school_id=school_id,
                type=SubscriptionType.SCHOOL,
                stripe_customer_id="",
                is_active=True,
                expiration=expiration,
                product_id=STAFF_COMP_PRODUCT_ID,
                info={"source": STAFF_COMP_GRANT_SOURCE},
            )
        )
    else:
        was_live = (
            existing.is_active
            and existing.expiration is not None
            and existing.expiration > now
        )
        expiration = max(existing.expiration or now, target)
        if not was_live or school.state != SchoolState.ACTIVE:
            outcome = "granted"
        elif expiration > existing.expiration:
            outcome = "extended"
        else:
            outcome = "unchanged"
        existing.is_active = True
        existing.expiration = expiration
        existing.info = {"source": STAFF_COMP_GRANT_SOURCE}

    if school.state != SchoolState.ACTIVE:
        school.state = SchoolState.ACTIVE

    description = (
        f"{school.name} was granted {days} days complimentary access "
        f"({outcome}) until {expiration:%Y-%m-%d}."
    )
    await event_repository.acreate(
        session=session,
        title=SCHOOL_COMP_GRANTED_EVENT_TITLE,
        description=description,
        info={
            "access_until": expiration.isoformat(),
            "campaign_id": campaign_id,
            "days": days,
            "granted_by": str(account.id) if account is not None else None,
            "idempotency_key": idempotency_key,
            "outcome": outcome,
            "previous_expiration": (
                previous_expiration.isoformat() if previous_expiration else None
            ),
            "reason": reason,
            "source": STAFF_COMP_GRANT_SOURCE,
            "state": school.state.value,
        },
        school=school,
        account=account,
        commit=False,
    )
    await session.commit()
    return StaffCompResult(
        outcome=outcome,
        state=school.state,
        access_until=expiration,
    )


async def grant_invite_access(
    session: AsyncSession, school: School, days: int
) -> tuple[str, datetime]:
    """Grant an invited school ``days`` of free access, once.

    Creates the comp grant (deterministic id ``comp_invite_<school_id>``) and
    flips the school to ACTIVE. A school gets **one** invite grant ever: if the
    row already exists it is a no-op (preventing repeat free trials via multiple
    inviters). The ``SELECT … FOR UPDATE`` on the deterministic id serialises
    concurrent accepts.

    Returns ``(outcome, expiration)`` where outcome is one of ``"activated"``
    (new grant), ``"already_active"`` (a pre-existing grant still live — idempotent
    re-accept) or ``"already_expired"`` (the one grant was already used and has
    lapsed; the caller must reject rather than report success).
    """
    now = datetime.utcnow()
    grant_id = invite_grant_id(school.wriveted_identifier)

    school = await lock_school_access_async(session, school.wriveted_identifier)
    if school is None:
        raise SchoolNotFoundError

    await ensure_comp_product_async(
        session, INVITE_GRANT_PRODUCT_ID, INVITE_GRANT_PRODUCT_NAME
    )

    existing = (
        await session.execute(
            select(Subscription).where(Subscription.id == grant_id).with_for_update()
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.is_active and existing.expiration and existing.expiration > now:
            return "already_active", existing.expiration
        return "already_expired", existing.expiration

    expiration = now + timedelta(days=days)
    grant = Subscription(
        id=grant_id,
        school_id=school.wriveted_identifier,
        type=SubscriptionType.SCHOOL,
        stripe_customer_id="",
        is_active=True,
        expiration=expiration,
        product_id=INVITE_GRANT_PRODUCT_ID,
        info={"source": INVITE_GRANT_SOURCE},
    )
    session.add(grant)

    if school.state != SchoolState.ACTIVE:
        school.state = SchoolState.ACTIVE
        session.add(school)

    await session.flush()
    return "activated", expiration


async def grant_invoice_pending_access(
    session: AsyncSession,
    school: School,
    expiration: datetime,
    *,
    billing_attempt_id: UUID,
) -> datetime:
    """Give a school net-terms access while its invoice is unpaid.

    Creates (or refreshes) the deterministic ``comp_invoice_pending_<school_id>``
    grant and flips the school ACTIVE, in the caller's transaction. The grant
    joins ``COMP_GRANT_SOURCES``, so paying the invoice retires it and an unpaid
    invoice lapses it via the sweep. Idempotent on the deterministic id; never
    shortens an existing expiry.
    """
    grant_id = invoice_pending_grant_id(school.wriveted_identifier)

    school = await lock_school_access_async(session, school.wriveted_identifier)
    if school is None:
        raise SchoolNotFoundError

    await ensure_comp_product_async(
        session, INVOICE_PENDING_PRODUCT_ID, INVOICE_PENDING_PRODUCT_NAME
    )

    existing = (
        await session.execute(
            select(Subscription).where(Subscription.id == grant_id).with_for_update()
        )
    ).scalar_one_or_none()
    if existing is None:
        session.add(
            Subscription(
                id=grant_id,
                school_id=school.wriveted_identifier,
                type=SubscriptionType.SCHOOL,
                stripe_customer_id="",
                is_active=True,
                expiration=expiration,
                product_id=INVOICE_PENDING_PRODUCT_ID,
                info={
                    "source": INVOICE_PENDING_GRANT_SOURCE,
                    "billing_attempt_id": str(billing_attempt_id),
                },
            )
        )
    else:
        expiration = max(existing.expiration or expiration, expiration)
        existing.is_active = True
        existing.expiration = expiration
        existing.info = {
            "source": INVOICE_PENDING_GRANT_SOURCE,
            "billing_attempt_id": str(billing_attempt_id),
        }

    if school.state != SchoolState.ACTIVE:
        school.state = SchoolState.ACTIVE
        session.add(school)

    await session.flush()
    return expiration

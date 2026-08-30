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

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.event import Event
from app.models.product import Product
from app.models.school import School, SchoolState
from app.models.service_account import ServiceAccount
from app.models.subscription import Subscription, SubscriptionType
from app.models.user import User
from app.repositories.event_repository import event_repository

# Mirrors stripe_events.CONTRIBUTION_GRANT_SOURCE (kept as a literal here to keep
# this a leaf module — stripe_events/internal API import COMP_GRANT_SOURCES from
# here, so importing it from stripe_events would be circular).
CONTRIBUTION_GRANT_SOURCE = "contribution_grant"
INVITE_GRANT_SOURCE = "invite_grant"
STAFF_COMP_GRANT_SOURCE = "staff_comp"
# Every comped (non-paying) grant source. The lapse sweep and Stripe-conversion
# retirement operate over this whole set, not just contributions.
COMP_GRANT_SOURCES = frozenset(
    {CONTRIBUTION_GRANT_SOURCE, INVITE_GRANT_SOURCE, STAFF_COMP_GRANT_SOURCE}
)

INVITE_GRANT_PRODUCT_ID = "comp_school_invite"
INVITE_GRANT_PRODUCT_NAME = "School invite (comped)"
INVITE_GRANT_SUBSCRIPTION_PREFIX = "comp_invite_"

STAFF_COMP_PRODUCT_ID = "comp_staff_grant"
STAFF_COMP_PRODUCT_NAME = "Staff complimentary grant"
STAFF_COMP_SUBSCRIPTION_PREFIX = "comp_staff_"
SCHOOL_COMP_GRANTED_EVENT_TITLE = "School complimentary access granted"

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

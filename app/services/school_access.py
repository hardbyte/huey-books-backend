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

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product import Product
from app.models.school import School, SchoolState
from app.models.subscription import Subscription, SubscriptionType

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


def invite_grant_id(school_wriveted_id) -> str:
    return f"{INVITE_GRANT_SUBSCRIPTION_PREFIX}{school_wriveted_id}"


def staff_comp_id(school_wriveted_id) -> str:
    return f"{STAFF_COMP_SUBSCRIPTION_PREFIX}{school_wriveted_id}"


async def grant_staff_comp(
    session: AsyncSession, school: School, days: int
) -> tuple[str, datetime]:
    """Staff action: give a school ``days`` of complimentary access, starting now.

    Unlike the one-per-school invite grant, this is re-grantable: repeating it
    extends the comp to the later of its current expiry and ``now + days`` (it
    never shortens existing access). The grant is a comp ``Subscription`` (source
    ``staff_comp``, so the lapse sweep expires it and a later Stripe subscription
    retires it) and flips the school to ACTIVE. Returns ``(outcome, expiration)``
    where outcome is ``"granted"`` (new) or ``"extended"`` (existing comp).
    """
    now = datetime.utcnow()
    grant_id = staff_comp_id(school.wriveted_identifier)
    target = now + timedelta(days=days)

    await session.merge(Product(id=STAFF_COMP_PRODUCT_ID, name=STAFF_COMP_PRODUCT_NAME))
    await session.flush()

    async def _apply(existing: Optional[Subscription]) -> tuple[str, datetime]:
        if existing is not None:
            # Never shorten an existing comp — extend to the later of the two.
            new_expiration = max(existing.expiration or now, target)
            existing.is_active = True
            existing.expiration = new_expiration
            existing.info = {"source": STAFF_COMP_GRANT_SOURCE}
            session.add(existing)
            return "extended", new_expiration
        session.add(
            Subscription(
                id=grant_id,
                school_id=school.wriveted_identifier,
                type=SubscriptionType.SCHOOL,
                stripe_customer_id="",
                is_active=True,
                expiration=target,
                product_id=STAFF_COMP_PRODUCT_ID,
                info={"source": STAFF_COMP_GRANT_SOURCE},
            )
        )
        return "granted", target

    existing = (
        await session.execute(
            select(Subscription).where(Subscription.id == grant_id).with_for_update()
        )
    ).scalar_one_or_none()

    try:
        # A savepoint so a concurrent first-insert (FOR UPDATE can't lock an
        # absent row) surfaces as a catchable IntegrityError, not a poisoned txn.
        async with session.begin_nested():
            outcome, expiration = await _apply(existing)
            await session.flush()
    except IntegrityError:
        existing = (
            await session.execute(
                select(Subscription)
                .where(Subscription.id == grant_id)
                .with_for_update()
            )
        ).scalar_one()
        outcome, expiration = await _apply(existing)
        await session.flush()

    if school.state != SchoolState.ACTIVE:
        school.state = SchoolState.ACTIVE
        session.add(school)

    await session.flush()
    return outcome, expiration


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

    # Seed the comp product (FK target for the grant) if absent.
    await session.merge(
        Product(id=INVITE_GRANT_PRODUCT_ID, name=INVITE_GRANT_PRODUCT_NAME)
    )
    await session.flush()

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

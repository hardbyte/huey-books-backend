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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product import Product
from app.models.school import School, SchoolState
from app.models.subscription import Subscription, SubscriptionType

# Mirrors stripe_events.CONTRIBUTION_GRANT_SOURCE (kept as a literal here to keep
# this a leaf module — stripe_events/internal API import COMP_GRANT_SOURCES from
# here, so importing it from stripe_events would be circular).
CONTRIBUTION_GRANT_SOURCE = "contribution_grant"
INVITE_GRANT_SOURCE = "invite_grant"
# Every comped (non-paying) grant source. The lapse sweep and Stripe-conversion
# retirement operate over this whole set, not just contributions.
COMP_GRANT_SOURCES = frozenset({CONTRIBUTION_GRANT_SOURCE, INVITE_GRANT_SOURCE})

INVITE_GRANT_PRODUCT_ID = "comp_school_invite"
INVITE_GRANT_PRODUCT_NAME = "School invite (comped)"
INVITE_GRANT_SUBSCRIPTION_PREFIX = "comp_invite_"


def invite_grant_id(school_wriveted_id) -> str:
    return f"{INVITE_GRANT_SUBSCRIPTION_PREFIX}{school_wriveted_id}"


async def grant_invite_access(
    session: AsyncSession, school: School, days: int
) -> tuple[str, datetime]:
    """Grant an invited school ``days`` of free access, once.

    Creates the comp grant (deterministic id ``comp_invite_<school_id>``) and
    flips the school to ACTIVE. A school gets **one** invite grant ever: if the
    row already exists (even expired), this is a no-op returning
    ``("already_granted", <existing expiration>)`` — preventing repeat free trials
    via multiple inviters. The ``SELECT … FOR UPDATE`` on the deterministic id
    serialises concurrent accepts.

    Returns ``(outcome, expiration)`` where outcome is ``"activated"`` (new grant)
    or ``"already_granted"`` (pre-existing).
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
        return "already_granted", existing.expiration

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

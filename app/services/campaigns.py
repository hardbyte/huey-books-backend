"""Campaign resolution: pick the best active campaign for a session context.

Resolution is two-stage:
  1. An indexable SQL prefilter narrows candidates by lifecycle, the active date
     window, structured targeting (country/region/school, age), and visibility.
  2. Candidates carrying a ``targeting_cel`` expression are then passed through a
     CEL gate, and the survivors are ranked by precedence in Python.

DB access is async and CEL evaluation is pure-compute (no I/O), so resolution is
safe to run inline in the request path.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import and_, func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from app.models.campaign import Campaign
from app.models.cms import ContentVisibility

logger = get_logger()


@dataclass
class CampaignContext:
    """The session facts a campaign is matched against."""

    now: datetime
    school_id: Optional[int] = None
    country_code: Optional[str] = None
    region_state: Optional[str] = None
    age: Optional[int] = None


def _dimension_matches(column, value):
    """True when the campaign places no restriction on this dimension, or the
    context value is in the campaign's allow-list."""
    unrestricted = or_(column.is_(None), func.cardinality(column) == 0)
    if value is None:
        # The campaign requires a value we don't have → only matches if unrestricted.
        return unrestricted
    return or_(unrestricted, column.any(value))


def _prefilter(context: CampaignContext):
    """Build the SQL WHERE clause for the structured prefilter + visibility."""
    window = and_(
        or_(Campaign.active_from.is_(None), Campaign.active_from <= context.now),
        or_(Campaign.active_until.is_(None), Campaign.active_until >= context.now),
    )

    if context.age is not None:
        age_match = and_(
            or_(Campaign.min_age.is_(None), Campaign.min_age <= context.age),
            or_(Campaign.max_age.is_(None), Campaign.max_age >= context.age),
        )
    else:
        # Unknown age → ignore age constraints rather than over-filter.
        age_match = true()

    # A session may be served WRIVETED- and PUBLIC-visibility campaigns globally,
    # and a SCHOOL-visibility campaign only for its own school. PRIVATE campaigns
    # are drafts (visible to their creator/admins for editing only) and never
    # auto-resolve onto a live session.
    visibility_conds = [
        Campaign.visibility.in_([ContentVisibility.WRIVETED, ContentVisibility.PUBLIC])
    ]
    if context.school_id is not None:
        visibility_conds.append(
            and_(
                Campaign.school_id == context.school_id,
                Campaign.visibility == ContentVisibility.SCHOOL,
            )
        )
    visible = or_(*visibility_conds)

    return and_(
        Campaign.is_active.is_(True),
        window,
        _dimension_matches(Campaign.country_codes, context.country_code),
        _dimension_matches(Campaign.region_states, context.region_state),
        _dimension_matches(Campaign.school_ids, context.school_id),
        age_match,
        visible,
    )


def _specificity(campaign: Campaign) -> int:
    """Most-specific-wins ordering: school > region > country > global."""
    if campaign.school_ids:
        return 3
    if campaign.region_states:
        return 2
    if campaign.country_codes:
        return 1
    return 0


def _passes_cel(campaign: Campaign, context: CampaignContext) -> bool:
    if not campaign.targeting_cel:
        return True
    from app.services.cel_evaluator import evaluate_cel_expression

    cel_context = {
        "school": {
            "id": context.school_id,
            "country": context.country_code,
            "state": context.region_state,
        },
        "user": {"age": context.age},
        "now": context.now.isoformat(),
    }
    try:
        return bool(evaluate_cel_expression(campaign.targeting_cel, cel_context))
    except Exception as exc:
        # Fail closed: a malformed targeting expression must not surface the campaign.
        logger.warning(
            "Campaign targeting_cel evaluation failed; skipping campaign",
            campaign_id=str(campaign.id),
            error=str(exc),
        )
        return False


async def get_campaign_boost_work_ids(
    session: AsyncSession, booklist_id: Optional[uuid.UUID]
) -> list[int]:
    """Work ids in a campaign's booklist, for the recommendation book-bias BOOST.

    Returns [] when there is no booklist, so callers can pass it through
    unconditionally.
    """
    if not booklist_id:
        return []
    from app.models.booklist_work_association import BookListItem

    rows = (
        (
            await session.execute(
                select(BookListItem.work_id).where(
                    BookListItem.booklist_id == booklist_id
                )
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def resolve_campaign(
    session: AsyncSession, context: CampaignContext
) -> Optional[Campaign]:
    """Return the highest-precedence active campaign for the context, or None."""
    candidates = (
        (await session.execute(select(Campaign).where(_prefilter(context))))
        .scalars()
        .all()
    )
    candidates = [c for c in candidates if _passes_cel(c, context)]
    if not candidates:
        return None
    candidates.sort(
        key=lambda c: (_specificity(c), c.priority, c.created_at),
        reverse=True,
    )
    return candidates[0]

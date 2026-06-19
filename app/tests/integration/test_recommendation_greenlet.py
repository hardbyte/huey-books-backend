"""Regression tests for the greenlet_spawn failures in the recommendation engine.

Background: the recommendation path intermittently 500'd /chat/start with
"greenlet_spawn has not been called; can't call await_only() here" — sync DB I/O
triggered under the async engine by a lazily-loaded relationship.

The operative culprit is the school-scoped path: ``School.collection`` is
``lazy="select"`` (true lazy), so reading it on a cold async session emits a
synchronous query and raises greenlet_spawn. The fix eager-loads it with
``selectinload(School.collection)``.

(``Work.authors`` / ``LabelSet.hues`` / ``LabelSet.reading_abilities`` are
``lazy="selectin"`` and load eagerly during execute, so the
``test_recommendation_rows_...`` case below is a smoke test of that consumption
path rather than a regression guard.)

These run against a *cold* async session — data is seeded/committed via the sync
session and read via a separate async session, so nothing is cached in the async
session's identity map (matching production, where the request session has not
already loaded these objects).
"""

import pytest

from app.api.recommendations import get_recommended_editions_and_labelsets
from app.models.labelset import RecommendStatus
from app.models.labelset_hue_association import LabelSetHue, Ordinal
from app.models.labelset_reading_ability_association import LabelSetReadingAbility
from app.repositories.labelset_repository import labelset_repository
from app.schemas.labelset import LabelSetDetail
from app.services.recommendations import get_recommended_labelset_query


@pytest.mark.asyncio
async def test_school_scoped_recommendation_does_not_greenlet(
    async_session, test_school_with_collection
):
    """Regression for the operative greenlet_spawn: resolving the school's
    (lazy) collection on a cold async session must not raise."""
    # Pre-fix this raised greenlet_spawn while reading school.collection.
    rows = await get_recommended_editions_and_labelsets(
        async_session,
        school_id=test_school_with_collection.id,
        hues=None,
        reading_abilities=None,
        age=None,
        recommendable_only=True,
        exclude_isbns=None,
    )
    assert rows is not None


@pytest.mark.asyncio
async def test_recommendation_rows_consumed_on_cold_session(
    session, async_session, works_list
):
    """Smoke test: building HueyBook-style data (authors, hues, reading
    abilities) from recommendation rows on a cold async session works."""
    for work in works_list[:5]:
        labelset = labelset_repository.create(
            session,
            obj_in={
                "min_age": 5,
                "max_age": 10,
                "huey_summary": "A good book",
                "recommend_status": RecommendStatus.GOOD,
            },
        )
        session.flush()
        work.labelset = labelset
        session.add(
            LabelSetHue(labelset_id=labelset.id, hue_id=1, ordinal=Ordinal.PRIMARY)
        )
        session.add(
            LabelSetReadingAbility(labelset_id=labelset.id, reading_ability_id=2)
        )
    session.commit()

    query = await get_recommended_labelset_query(async_session, recommendable_only=True)
    rows = (await async_session.execute(query.limit(5))).all()
    assert rows, "expected at least one recommendable book to be returned"

    for work, edition, labelset in rows:
        assert work.get_authors_string() is not None
        assert LabelSetDetail.model_validate(labelset) is not None

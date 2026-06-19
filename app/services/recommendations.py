from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from structlog import get_logger

from app import crud
from app.models import (
    CollectionItem,
    Edition,
    Hue,
    LabelSet,
    LabelSetHue,
    LabelSetReadingAbility,
    ReadingAbility,
    Work,
)
from app.models.labelset import RecommendStatus
from app.schemas.recommendations import ReadingAbilityKey

logger = get_logger()


async def get_recommended_labelset_query(
    asession: AsyncSession,
    hues: Optional[list[str]] = None,
    collection_id: Optional[int] = None,
    age: Optional[int] = None,
    reading_abilities: Optional[list[str]] = None,
    recommendable_only: Optional[bool] = True,
    exclude_isbns: Optional[list[str]] = None,
):
    """
    Return a select query for labelsets filtering by hue, collection, age, and reading ability.
    Filters for recommendable only items and excludes certain ISBNs.
    The query uses a CTE for latest labelsets and orders results randomly.
    """
    latest_labelset_subquery = (
        select(LabelSet)
        .distinct(LabelSet.work_id)
        .order_by(LabelSet.work_id, LabelSet.id.desc())
        .cte(name="latestlabelset")
    )
    aliased_labelset = aliased(LabelSet, latest_labelset_subquery)
    query = (
        select(Work, Edition, aliased_labelset)
        .select_from(aliased_labelset)
        .distinct(Work.id)
        .order_by(Work.id)
        .join(Work, aliased_labelset.work_id == Work.id)
        .join(Edition, Edition.work_id == Work.id)
        .join(LabelSetHue, LabelSetHue.labelset_id == aliased_labelset.id)
        .join(
            LabelSetReadingAbility,
            LabelSetReadingAbility.labelset_id == aliased_labelset.id,
        )
    )

    # For debugging, print the query plan
    # from app.db.explain import explain
    # if False:
    #     # result = await asession.execute(query.limit(1000))
    #     explain_results = (
    #         (await asession.execute(explain(query, analyze=True))).scalars().all()
    #     )
    #     logger.info("Query plan")
    #     for entry in explain_results:
    #         logger.info(entry)
    #

    # Now add the optional filters
    if collection_id is not None:
        # Filter for works in a collection
        collection = await crud.collection.aget_or_404(db=asession, id=collection_id)
        query = (
            query.join(
                CollectionItem, CollectionItem.edition_isbn == Edition.isbn
            ).where(CollectionItem.collection == collection)
            # Could order by other things, but consider indexes
            # .order_by(Work.id, CollectionItem.copies_available.desc())
        )

    if hues is not None and len(hues) > 0:
        # Labelset Ids from hues
        hue_ids_query = select(Hue.id).where(Hue.key.in_(hues))
        query = query.where(LabelSetHue.hue_id.in_(hue_ids_query))

    if reading_abilities is not None and len(reading_abilities) > 0:
        # Labelset Ids from reading abilities
        reading_ability_ids_query = select(ReadingAbility.id).where(
            ReadingAbility.key.in_(reading_abilities)
        )
        query = query.where(
            LabelSetReadingAbility.reading_ability_id.in_(reading_ability_ids_query)
        )

    if age is not None:
        query = query.where(aliased_labelset.min_age <= age).where(
            aliased_labelset.max_age >= age
        )

    # Add other filtering criteria
    if recommendable_only:
        query = query.where(aliased_labelset.recommend_status == RecommendStatus.GOOD)

    query = query.where(Edition.cover_url.is_not(None)).limit(10_000)

    # To exclude images from the OpenLibrary bucket folder
    # query = query.filter(~Edition.cover_url.contains("/open/"))

    # exclude certain editions using isbn
    if exclude_isbns is not None and len(exclude_isbns) > 0:
        query = query.where(~Edition.isbn.in_(exclude_isbns))

    # Now make a massive CTE so we can shuffle the results
    massive_cte = query.cte(name="labeled")

    aliased_work = aliased(Work, massive_cte)
    aliased_edition = aliased(Edition, massive_cte)
    aliased_labelset_end = aliased(LabelSet, massive_cte)

    return select(aliased_work, aliased_edition, aliased_labelset_end).order_by(
        func.random()
    )


def gen_next_reading_ability(input: ReadingAbilityKey, decrement: bool = False):
    """
    Generates a reading ability level equivalent to 1 increment up (optionally down).
    """
    # since ReadingAbilityKey is a 3.7+ enum.Enum type, it remembers natural definition order
    # we can treat this as defacto indexing (allowing us to increment or decrement a reading ability)
    reading_ability_key_list = [v.value for v in ReadingAbilityKey]
    current_reading_ability_index = reading_ability_key_list.index(input)

    if not decrement:
        # don't increment more than the highest level. harry_potter + 1 = harry_potter
        next_reading_ability_index = min(
            len(reading_ability_key_list) - 1, current_reading_ability_index + 1
        )
    else:
        # don't decrement below than the lowest level. spot - 1 = spot
        next_reading_ability_index = max(0, current_reading_ability_index - 1)

    return ReadingAbilityKey(reading_ability_key_list[next_reading_ability_index])

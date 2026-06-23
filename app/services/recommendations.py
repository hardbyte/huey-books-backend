from typing import Optional

from sqlalchemy import cast, func, literal, select
from sqlalchemy.dialects.postgresql import ARRAY, VARCHAR
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
from app.models.collection import Collection
from app.models.labelset import RecommendStatus
from app.schemas.recommendations import ReadingAbilityKey

logger = get_logger()


async def get_recommended_editions_from_mv(
    asession: AsyncSession,
    hues: Optional[list[str]] = None,
    school_id: Optional[int] = None,
    age: Optional[int] = None,
    reading_abilities: Optional[list[str]] = None,
    recommendable_only: Optional[bool] = True,
    exclude_isbns: Optional[list[str]] = None,
    limit: int = 10,
) -> list[tuple[Work, Edition, LabelSet]]:
    """
    Single scored query over the recommendable_editions materialized view.

    Scoring weights (higher = more important):
      - school_collection_match: 4  — the work's cover edition is in the school's collection
      - reading_ability_match:   2  — the labelset covers at least one requested reading ability
      - hue_match:               1  — the labelset covers at least one requested hue

    Results are ordered by score DESC, then random() within each score tier.
    All hard filters (recommend_status, age, exclude_isbns) are always applied.
    """
    from sqlalchemy import case, column, table

    # Refer to the MV as a plain table expression rather than ORM so we can
    # use the pre-aggregated array columns without extra joins.
    mv = table(
        "recommendable_editions",
        column("work_id"),
        column("labelset_id"),
        column("min_age"),
        column("max_age"),
        column("recommend_status"),
        column("cover_edition_isbn"),
        column("cover_url"),
        column("hue_keys"),
        column("reading_ability_keys"),
    )

    # --- Scoring expressions ---
    # Each criterion contributes its weight when matched, 0 otherwise.
    # hue_keys / reading_ability_keys are varchar[] in the MV (from varchar(50) source
    # columns), so the array overlap operator requires varchar[] on both sides.
    hue_score = literal(0)
    if hues and len(hues) > 0:
        hue_arr = cast(hues, ARRAY(VARCHAR(50)))
        hue_score = case(
            (mv.c.hue_keys.op("&&")(hue_arr), literal(1)),
            else_=literal(0),
        )

    ra_score = literal(0)
    if reading_abilities and len(reading_abilities) > 0:
        ra_arr = cast(reading_abilities, ARRAY(VARCHAR(50)))
        ra_score = case(
            (mv.c.reading_ability_keys.op("&&")(ra_arr), literal(2)),
            else_=literal(0),
        )

    # School-collection membership via a lateral EXISTS sub-query so we avoid
    # an outer join that might multiply rows (a work may have multiple
    # collection_items if the same isbn appears in different collections).
    # Note: Collection.school_id is a FK to schools.wriveted_identifier (UUID),
    # so we join through the School model to convert the integer school PK.
    school_score = literal(0)
    if school_id is not None:
        from app.models.school import School as SchoolModel

        school_exists = (
            select(literal(1))
            .select_from(CollectionItem)
            .join(Collection, Collection.id == CollectionItem.collection_id)
            .join(SchoolModel, SchoolModel.wriveted_identifier == Collection.school_id)
            .where(
                CollectionItem.edition_isbn == mv.c.cover_edition_isbn,
                SchoolModel.id == school_id,
            )
            .correlate(mv)
            .exists()
        )
        school_score = case(
            (school_exists, literal(4)),
            else_=literal(0),
        )

    total_score = (hue_score + ra_score + school_score).label("score")

    # --- Build the scored MV query ---
    scored_q = (
        select(
            mv.c.work_id,
            mv.c.labelset_id,
            mv.c.cover_edition_isbn,
            total_score,
        )
        .select_from(mv)
        .order_by(total_score.desc(), func.random())
        .limit(limit)
    )

    # Hard filters
    # recommend_status is stored as the PostgreSQL native enum type "recommendstatus"
    # in the MV.  Cast the column to text so the equality comparison uses the
    # plain text = text operator rather than the missing enum = varchar operator.
    if recommendable_only:
        scored_q = scored_q.where(
            cast(mv.c.recommend_status, VARCHAR) == RecommendStatus.GOOD.value
        )

    if age is not None:
        scored_q = scored_q.where(mv.c.min_age <= age).where(mv.c.max_age >= age)

    if exclude_isbns and len(exclude_isbns) > 0:
        scored_q = scored_q.where(mv.c.cover_edition_isbn.notin_(exclude_isbns))

    # Execute the scored MV query
    mv_rows = (await asession.execute(scored_q)).all()

    if not mv_rows:
        return []

    work_ids = [row.work_id for row in mv_rows]
    labelset_ids = [row.labelset_id for row in mv_rows]
    edition_isbns = [row.cover_edition_isbn for row in mv_rows]

    # Reload ORM objects to preserve the (Work, Edition, LabelSet) tuple contract
    # that downstream callers (get_recommendations_with_fallback) expect.
    works_q = select(Work).where(Work.id.in_(work_ids))
    editions_q = select(Edition).where(Edition.isbn.in_(edition_isbns))
    labelsets_q = select(LabelSet).where(LabelSet.id.in_(labelset_ids))

    works_by_id = {
        row.id: row for row in (await asession.execute(works_q)).scalars().all()
    }
    editions_by_isbn = {
        row.isbn: row for row in (await asession.execute(editions_q)).scalars().all()
    }
    labelsets_by_id = {
        row.id: row for row in (await asession.execute(labelsets_q)).scalars().all()
    }

    # Reassemble in score order (mv_rows is already ordered)
    result = []
    for row in mv_rows:
        work = works_by_id.get(row.work_id)
        edition = editions_by_isbn.get(row.cover_edition_isbn)
        labelset = labelsets_by_id.get(row.labelset_id)
        if work is not None and edition is not None and labelset is not None:
            result.append((work, edition, labelset))

    return result


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

    Retained for backward-compatibility (used by existing test helpers).
    New code should prefer get_recommended_editions_from_mv.
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

    if collection_id is not None:
        collection = await crud.collection.aget_or_404(db=asession, id=collection_id)
        query = query.join(
            CollectionItem, CollectionItem.edition_isbn == Edition.isbn
        ).where(CollectionItem.collection == collection)

    if hues is not None and len(hues) > 0:
        hue_ids_query = select(Hue.id).where(Hue.key.in_(hues))
        query = query.where(LabelSetHue.hue_id.in_(hue_ids_query))

    if reading_abilities is not None and len(reading_abilities) > 0:
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

    if recommendable_only:
        query = query.where(aliased_labelset.recommend_status == RecommendStatus.GOOD)

    query = query.where(Edition.cover_url.is_not(None)).limit(10_000)

    if exclude_isbns is not None and len(exclude_isbns) > 0:
        query = query.where(~Edition.isbn.in_(exclude_isbns))

    massive_cte = query.cte(name="labeled")

    aliased_work = aliased(Work, massive_cte)
    aliased_edition = aliased(Edition, massive_cte)
    aliased_labelset_end = aliased(LabelSet, massive_cte)

    return select(aliased_work, aliased_edition, aliased_labelset_end).order_by(
        func.random()
    )


async def enqueue_debounced_mv_refresh() -> None:
    """
    Enqueue a Cloud Tasks job to refresh the recommendable_editions MV.

    Debounce / coalescing strategy: Cloud Tasks supports named tasks; a task with
    a given name can only be created once within a ~4-hour deduplication window
    (GCP enforces this).  We use a fixed task name
    ``refresh-recommendable-editions`` so that any number of labelset/collection
    write events that arrive within that window collapse into a single refresh.
    The task is scheduled with a short delay (60 s) so that a burst of writes
    during a bulk-labelling session results in one refresh shortly after the burst
    ends rather than many back-to-back refreshes.

    Tradeoff: the deduplication window is GCP-managed (~4 h after the task
    executes or is deleted), so a second bulk-labelling session starting within
    that window would not trigger a new refresh until the window expires.  For the
    expected usage patterns (weekly labelling runs) this is acceptable.  The weekly
    Cloud Scheduler job ensures the MV is always eventually consistent.

    If Cloud Tasks is not configured (GCP_CLOUD_TASKS_NAME is None or
    WRIVETED_INTERNAL_API is not set) the call is a no-op so local development
    and test environments are unaffected.

    Wiring: call this function (fire-and-forget, don't await in critical path)
    from labelset/review mutation endpoints after committing.  Example callers:
      - app/api/labelset.py::bulk_patch_labelsets (after session.commit)
      - app/api/reviews.py (after a review-promoted labelset update)
    """
    from app.config import get_settings

    settings = get_settings()
    if not settings.GCP_CLOUD_TASKS_NAME or not settings.WRIVETED_INTERNAL_API:
        logger.debug("Cloud Tasks not configured; skipping MV refresh enqueue")
        return

    try:
        import datetime

        from google.cloud import tasks_v2
        from google.protobuf import timestamp_pb2

        client = tasks_v2.CloudTasksClient()
        parent = client.queue_path(
            settings.GCP_PROJECT_ID,
            settings.GCP_LOCATION,
            settings.GCP_CLOUD_TASKS_NAME,
        )
        # Named task for deduplication — GCP rejects duplicate names within ~4 h
        task_name = f"{parent}/tasks/refresh-recommendable-editions"

        schedule_time = datetime.datetime.utcnow() + datetime.timedelta(seconds=60)
        timestamp = timestamp_pb2.Timestamp()
        timestamp.FromDatetime(schedule_time)

        task = {
            "name": task_name,
            "schedule_time": timestamp,
            "http_request": {
                "http_method": tasks_v2.HttpMethod.POST,
                "url": f"{settings.WRIVETED_INTERNAL_API}/v1/maintenance/refresh-recommendations",
                "headers": {"Content-Type": "application/json"},
                "body": b"{}",
                "oidc_token": {
                    "service_account_email": settings.GCP_CLOUD_TASKS_SERVICE_ACCOUNT,
                },
            },
        }

        client.create_task(request={"parent": parent, "task": task})
        logger.info("Enqueued debounced MV refresh task")

    except Exception as exc:
        # A failed enqueue should never abort the primary write that triggered it.
        logger.warning("Failed to enqueue MV refresh task", error=str(exc))


def gen_next_reading_ability(input: ReadingAbilityKey, decrement: bool = False):
    """
    Generates a reading ability level equivalent to 1 increment up (optionally down).
    """
    reading_ability_key_list = [v.value for v in ReadingAbilityKey]
    current_reading_ability_index = reading_ability_key_list.index(input)

    if not decrement:
        next_reading_ability_index = min(
            len(reading_ability_key_list) - 1, current_reading_ability_index + 1
        )
    else:
        next_reading_ability_index = max(0, current_reading_ability_index - 1)

    return ReadingAbilityKey(reading_ability_key_list[next_reading_ability_index])

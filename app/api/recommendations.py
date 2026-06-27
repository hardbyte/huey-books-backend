import json
from typing import Any, Optional, Tuple

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from app.api.dependencies.async_db_dep import DBSessionDep
from app.api.dependencies.security import get_current_active_user_or_service_account
from app.config import get_settings
from app.models import EventLevel, School
from app.repositories.event_repository import event_repository
from app.repositories.school_repository import school_repository
from app.schemas.labelset import LabelSetDetail
from app.schemas.recommendations import HueyBook, HueyOutput, HueyRecommendationFilter
from app.services.recommendations import get_recommended_editions_from_mv

router = APIRouter(
    tags=["Recommendations"],
    dependencies=[Depends(get_current_active_user_or_service_account)],
)
logger = get_logger()
config = get_settings()


@router.post("/recommend", response_model=HueyOutput)
async def get_recommendations(
    asession: DBSessionDep,
    data: HueyRecommendationFilter,
    background_tasks: BackgroundTasks,
    limit: Optional[int] = Query(5, description="Maximum number of items to return"),
    account=Depends(get_current_active_user_or_service_account),
):
    """
    Fetch labeled works as recommended by Huey.

    Returns recommendations ordered by relevance score (school-collection match,
    reading-ability match, hue match), then random within each score tier.
    """
    logger.debug("Recommendation endpoint called", parameters=data)

    if data.wriveted_identifier is not None:
        school = await school_repository.aget_by_wriveted_id_or_404(
            db=asession, wriveted_id=data.wriveted_identifier
        )
    else:
        school = None

    recommended_books, query_parameters = await get_recommendations_with_fallback(
        asession,
        account,
        school,
        data=data,
        background_tasks=background_tasks,
        limit=limit,
    )
    return HueyOutput(
        count=len(recommended_books),
        query=query_parameters,
        books=recommended_books,
    )


async def get_recommendations_with_fallback(
    asession: AsyncSession,
    account,
    school: School,
    data: HueyRecommendationFilter,
    background_tasks: BackgroundTasks,
    limit=5,
    remove_duplicate_authors=True,
    boost_work_ids=None,
) -> Tuple[list[HueyBook], Any]:
    """
    Return (filtered_books, query_parameters) by running a single scored query
    over the recommendable_editions materialized view.

    The MV query applies hard filters (recommend_status, age, exclude_isbns) and
    scores candidates by soft criteria (school-collection membership weighted 4,
    reading-ability overlap weighted 2, hue overlap weighted 1), then orders by
    score DESC + random() within each tier.  This replaces the previous four
    sequential fallback passes.
    """
    school_id = school.id if school is not None else None
    query_parameters = {
        "school_id": school_id,
        "hues": data.hues or [],
        "reading_abilities": data.reading_abilities or [],
        "age": data.age,
        "recommendable_only": data.recommendable_only,
        "exclude_isbns": data.exclude_isbns or [],
        "boost_work_ids": boost_work_ids or [],
        "limit": limit + 5,
    }
    logger.info("About to make a recommendation", query_parameters=query_parameters)

    row_results = await get_recommended_editions_and_labelsets(
        asession, **query_parameters
    )
    logger.debug("Have got recommendation results from database")

    # Note the row_results are an iterable of (work, edition, labelset) orm instances
    # Now we convert that to a list of HueyBook instances:
    recommended_books = [
        HueyBook(
            work_id=work.id,
            isbn=edition.isbn,
            cover_url=edition.cover_url,
            display_title=edition.get_display_title(),
            authors_string=work.get_authors_string(),
            summary=labelset.huey_summary,
            labels=LabelSetDetail.model_validate(labelset),
        )
        for (work, edition, labelset) in row_results
    ]
    filtered_books = []
    if len(recommended_books) > 1:
        if remove_duplicate_authors:
            current_authors = set()
            for book in recommended_books:
                if book.authors_string not in current_authors:
                    current_authors.add(book.authors_string)
                    filtered_books.append(book)
                else:
                    logger.info(
                        "Removing book recommendation by author that is already being recommended",
                        author=book.authors_string,
                    )
                if len(filtered_books) >= limit:
                    break

        event_recommendation_data = [json.loads(b.json()) for b in filtered_books[:10]]

        await event_repository.acreate(
            asession,
            title="Made a recommendation",
            description=f"Made a recommendation of {len(filtered_books)} books",
            info={
                "recommended": event_recommendation_data,
                "query_parameters": query_parameters,
            },
            school=school,
            account=account,
        )
    else:
        if len(row_results) == 0:
            await event_repository.acreate(
                asession,
                title="No books",
                description="No books met the criteria for recommendation",
                info={"query_parameters": query_parameters},
                school=school,
                account=account,
                level=EventLevel.WARNING,
            )
    return filtered_books, query_parameters


async def get_recommended_editions_and_labelsets(
    asession: AsyncSession,
    school_id,
    hues,
    reading_abilities,
    age,
    recommendable_only,
    exclude_isbns,
    boost_work_ids=None,
    limit=5,
):
    """
    Fetch (Work, Edition, LabelSet) tuples from the recommendable_editions MV.

    The MV pre-computes one row per work (latest labelset, one cover edition,
    aggregated hue/reading-ability keys), so this query avoids the expensive
    multi-join + DISTINCT ON that caused the original 16-second plans.
    """
    return await get_recommended_editions_from_mv(
        asession,
        hues=hues,
        school_id=school_id,
        age=age,
        reading_abilities=reading_abilities,
        recommendable_only=recommendable_only,
        exclude_isbns=exclude_isbns,
        boost_work_ids=boost_work_ids,
        limit=limit,
    )

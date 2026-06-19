import json
from typing import Any, Optional, Tuple

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from structlog import get_logger

from app.api.dependencies.async_db_dep import DBSessionDep
from app.api.dependencies.security import get_current_active_user_or_service_account
from app.config import get_settings
from app.models import EventLevel, School
from app.repositories.event_repository import event_repository
from app.repositories.school_repository import school_repository
from app.schemas.labelset import LabelSetDetail
from app.schemas.recommendations import (
    HueyBook,
    HueyOutput,
    HueyRecommendationFilter,
    ReadingAbilityKey,
)
from app.services.recommendations import get_recommended_labelset_query

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

    Note this endpoint returns recommendations in a random order.
    """
    logger.debug("Recommendation endpoint called", parameters=data)

    if data.wriveted_identifier is not None:
        school = await school_repository.aget_by_wriveted_id_or_404(
            db=asession, wriveted_id=data.wriveted_identifier
        )
        # TODO check account is allowed to `read` school
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
) -> Tuple[list[HueyBook], Any]:
    """
    Returns a tuple containing:
    - a list of HueyBook instances,
    - final set of query parameters used
    """
    school_id = school.id if school is not None else None
    query_parameters = {
        "school_id": school_id,
        "hues": data.hues or [],
        "reading_abilities": data.reading_abilities or [],
        "age": data.age,
        "recommendable_only": data.recommendable_only,
        "exclude_isbns": data.exclude_isbns or [],
        "limit": limit + 5,
    }
    logger.info("About to make a recommendation", query_parameters=query_parameters)
    row_results = await get_recommended_editions_and_labelsets(
        asession, **query_parameters
    )
    logger.debug("Have got recommendation results from database")
    fallback_level = 0
    if data.fallback and len(row_results) < 3:
        fallback_level += 1
        # proper fallback logic can come later when booklists are implemented
        query_parameters["school_id"] = None
        logger.info(
            f"Desired query returned {len(row_results)} books. Trying fallback method 1 of looking outside school collection",
            query_parameters=query_parameters,
        )
        row_results = await get_recommended_editions_and_labelsets(
            asession, **query_parameters
        )
    if len(row_results) < 3:
        logger.debug(
            "Still not enough, alright let's recommend outside the school collection"
        )
        fallback_level += 1
        # Let's just include all the hues and try the query again.
        query_parameters["hues"] = None
        logger.info(
            f"Desired query returned {len(row_results)} books. Trying fallback method 2 of including all hues",
            query_parameters=query_parameters,
        )
        row_results = await get_recommended_editions_and_labelsets(
            asession, **query_parameters
        )
    if len(row_results) < 3:
        fallback_level += 1
        logger.debug("Widen the reading ability")
        if len(query_parameters["reading_abilities"]) == 1:
            match query_parameters["reading_abilities"][0]:
                case ReadingAbilityKey.HARRY_POTTER:
                    query_parameters["reading_abilities"].append(
                        ReadingAbilityKey.CHARLIE_CHOCOLATE
                    )
                case ReadingAbilityKey.SPOT:
                    query_parameters["reading_abilities"].append(
                        ReadingAbilityKey.CAT_HAT
                    )
                case _:
                    query_parameters["reading_abilities"].append(
                        ReadingAbilityKey.TREEHOUSE
                    )
            logger.info(
                f"Desired query returned {len(row_results)} books. Trying fallback method 3 of including widening the reading ability",
                query_parameters=query_parameters,
            )
        elif query_parameters["age"] is not None:
            logger.warning("Incrementing age")
            query_parameters["age"] += 2
            logger.info(
                f"Desired query returned {len(row_results)} books. Trying fallback method of increasing the age",
                query_parameters=query_parameters,
            )
        else:
            logger.warning("No recommendations available")
        row_results = await get_recommended_editions_and_labelsets(
            asession, **query_parameters
        )

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
            # While we have more than the desired number of books, remove any works from the same author
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

        # Bit annoying to dump and load json here but we want to fully serialize the JSON ready for
        # inserting into postgreSQL, which BaseModel.dict() doesn't do
        event_recommendation_data = [json.loads(b.json()) for b in filtered_books[:10]]

        await event_repository.acreate(
            asession,
            title="Made a recommendation",
            description=f"Made a recommendation of {len(filtered_books)} books",
            info={
                "recommended": event_recommendation_data,
                "query_parameters": query_parameters,
                "fallback_level": fallback_level,
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
                info={
                    "query_parameters": query_parameters,
                    "fallback_level": fallback_level,
                },
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
    limit=5,
):
    collection_id = None
    if school_id is not None:
        # Eager-load the collection: school_repository.aget(id) does not, and
        # accessing the lazy School.collection afterwards raises greenlet_spawn
        # under the async engine.
        school = (
            await asession.execute(
                select(School)
                .where(School.id == school_id)
                .options(selectinload(School.collection))
            )
        ).scalar_one_or_none()
        logger.debug("Recommendation request for school", school=school)
        if school is not None and school.collection is not None:
            collection_id = school.collection.id
        else:
            logger.warning(
                "School recommendation was requested, but school doesn't have a collection!",
                school=school,
            )

    query = await get_recommended_labelset_query(
        asession,
        hues=hues,
        collection_id=collection_id,
        age=age,
        reading_abilities=reading_abilities,
        recommendable_only=recommendable_only,
        exclude_isbns=exclude_isbns,
    )

    logger.debug("Recommendation query prepared")

    # if True:
    #     explain_results = (
    #         (await asession.execute(explain(query, analyze=True))).scalars().all()
    #     )
    #     logger.info("Query plan")
    #     for entry in explain_results:
    #         logger.info(entry)

    row_results = (await asession.execute(query.limit(limit))).all()
    return row_results

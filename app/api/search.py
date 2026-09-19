from time import perf_counter
from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.params import Query
from structlog import get_logger

from app.api.common.pagination import PaginatedQueryParams
from app.api.dependencies.async_db_dep import DBSessionDep
from app.api.dependencies.security import get_current_active_user_or_service_account
from app.schemas.search_results import SearchQueryInput, SearchResults
from app.services.search import book_search

router = APIRouter(
    tags=["Books"], dependencies=[Depends(get_current_active_user_or_service_account)]
)

logger = get_logger()


@router.get(
    "/search",
    response_model=SearchResults,
)
async def get_book_search(
    session: DBSessionDep,
    query: str = Query(None, description="Query string"),
    author_id: Optional[int] = Query(None, description="Author's Wriveted Id"),
    # type: Optional[WorkType] = Query(WorkType.BOOK),
    pagination: PaginatedQueryParams = Depends(),
):
    started = perf_counter()
    results = await book_search(
        session, query_param=query, pagination=pagination, author_id=author_id
    )
    logger.info(
        "Search query completed",
        duration_ms=round((perf_counter() - started) * 1000, 2),
        result_count=len(results),
        query_present=query is not None,
        limit=pagination.limit,
    )
    return SearchResults(
        input=SearchQueryInput(query=query, author_id=author_id),
        books=results,
    )

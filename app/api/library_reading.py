from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.exc import DBAPIError

from app.api.common.workspace_errors import WorkspaceRoute
from app.api.dependencies.async_db_dep import DBSessionDep
from app.api.dependencies.security import get_current_active_user
from app.models.user import User
from app.repositories.review_repository import review_repository
from app.schemas.pagination import PaginatedResponse, Pagination
from app.schemas.review import ReviewQueueItem
from app.schemas.school_insights import InsightsWeeks, SchoolInsights
from app.services.organisation_workspace import resolve_library
from app.services.school_insights import get_school_insights

router = APIRouter(tags=["Library reading"], route_class=WorkspaceRoute)
Actor = Annotated[User, Depends(get_current_active_user)]


@router.get("/libraries/{library_uuid}/insights", response_model=SchoolInsights)
async def library_insights(
    library_uuid: UUID,
    response: Response,
    session: DBSessionDep,
    actor: Actor,
    weeks: InsightsWeeks = Query(InsightsWeeks.FOUR),
):
    """Library aggregate metrics; school_uuid and school_name are compatibility fields."""
    response.headers["Cache-Control"] = "private, no-store"
    access = await resolve_library(session, actor, library_uuid, "insights")
    try:
        return await get_school_insights(session, access.library, weeks)
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == "57014":
            raise HTTPException(
                503, "Insights took too long. Please try again later."
            ) from exc
        raise


@router.get(
    "/libraries/{library_uuid}/review-queue",
    response_model=PaginatedResponse[ReviewQueueItem],
)
async def library_review_queue(
    library_uuid: UUID,
    response: Response,
    session: DBSessionDep,
    actor: Actor,
    status: str = Query("all", pattern="^(unchecked|ai_labelled|human_reviewed|all)$"),
    min_school_count: int = Query(0, ge=0),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    """Works held at the requested library across all of its collections."""
    response.headers["Cache-Control"] = "private, no-store"
    access = await resolve_library(session, actor, library_uuid, "review")
    items, total = await review_repository.get_review_queue(
        db=session,
        school_id=access.library.id,
        status=status,
        min_school_count=min_school_count,
        skip=skip,
        limit=limit,
    )
    return PaginatedResponse(
        data=[ReviewQueueItem(**item) for item in items],
        pagination=Pagination(skip=skip, limit=limit, total=total),
    )

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy.exc import DBAPIError

from app.api.dependencies.async_db_dep import DBSessionDep
from app.api.dependencies.school import aget_school_from_uuid
from app.models.school import School
from app.permissions import Permission
from app.schemas.school_insights import InsightsWeeks, SchoolInsights
from app.services.school_insights import get_school_insights

router = APIRouter(tags=["School insights"])


@router.get("/school/{school_uuid}/insights", response_model=SchoolInsights)
async def school_insights(
    response: Response,
    session: DBSessionDep,
    school: School = Permission("insights", aget_school_from_uuid),
    weeks: InsightsWeeks = Query(InsightsWeeks.FOUR),
):
    response.headers["Cache-Control"] = "private, no-store"
    try:
        return await get_school_insights(session, school, weeks)
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == "57014":
            raise HTTPException(
                503, "Insights took too long. Please try again later."
            ) from exc
        raise

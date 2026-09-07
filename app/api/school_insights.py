from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy.exc import DBAPIError

from app.api.dependencies.async_db_dep import DBSessionDep
from app.api.dependencies.school import aget_school_from_wriveted_id
from app.models.school import School
from app.permissions import Permission
from app.schemas.school_insights import SchoolInsights
from app.services.school_insights import get_school_insights

router = APIRouter(tags=["School insights"])


@router.get("/school/{wriveted_identifier}/insights", response_model=SchoolInsights)
async def school_insights(
    response: Response,
    session: DBSessionDep,
    school: School = Permission("insights", aget_school_from_wriveted_id),
    weeks: int = Query(4, ge=4, le=26),
):
    if weeks not in (4, 12, 26):
        raise HTTPException(422, "Choose 4, 12 or 26 complete weeks")
    response.headers["Cache-Control"] = "private, no-store"
    try:
        return await get_school_insights(session, school, weeks)
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == "57014":
            raise HTTPException(
                503, "Insights took too long. Please try again later."
            ) from exc
        raise

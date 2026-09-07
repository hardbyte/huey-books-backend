from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.school import School
from app.repositories import school_insights as repository
from app.schemas.school_insights import Engagement, SchoolInsights

PRIVACY_THRESHOLD = 5


def small(count: int) -> bool:
    return 0 < count < PRIVACY_THRESHOLD


def summarize(
    rows: list[dict], start: datetime, weeks: int
) -> tuple[Engagement, list[dict]]:
    totals = {
        key: sum(row[key] for row in rows)
        for key in (
            "sessions",
            "reached",
            "feedback",
            "liked",
            "disliked",
            "already_read",
            "liked_sessions",
            "disliked_sessions",
            "read_sessions",
        )
    }
    sessions, reached, feedback = (
        totals[key] for key in ("sessions", "reached", "feedback")
    )
    hide_sessions = small(sessions)
    hide_reached = hide_sessions or small(reached) or small(sessions - reached)
    hide_feedback = hide_reached or small(feedback) or small(reached - feedback)
    hide_categories = (
        hide_feedback
        or any(row.get("unverified_feedback", 0) for row in rows)
        or any(
            small(totals[key])
            for key in ("liked_sessions", "disliked_sessions", "read_sessions")
        )
    )
    engagement = Engagement(
        sessions=None if hide_sessions else sessions,
        reached_recommendations=None if hide_reached else reached,
        recommendation_rate=None
        if hide_reached or not sessions
        else round(reached / sessions, 4),
        feedback_sessions=None if hide_feedback else feedback,
        liked=None if hide_categories else totals["liked"],
        disliked=None if hide_categories else totals["disliked"],
        already_read=None if hide_categories else totals["already_read"],
    )
    # Hide the entire breakdown so a total cannot reveal one suppressed bucket.
    if hide_sessions or any(small(row["sessions"]) for row in rows):
        return engagement, []
    by_week = {row["week"]: row["sessions"] for row in rows}
    trends = [
        {
            "week": (start + timedelta(weeks=offset)).date(),
            "sessions": by_week.get((start + timedelta(weeks=offset)).date(), 0),
        }
        for offset in range(weeks)
    ]
    return engagement, trends


async def get_school_insights(
    db: AsyncSession, school: School, weeks: int
) -> SchoolInsights:
    now = datetime.now(timezone.utc)
    end = datetime.combine(
        now.date() - timedelta(days=now.weekday()), datetime.min.time()
    )
    start = end - timedelta(weeks=weeks)
    parameters = repository.query_parameters(school.wriveted_identifier, start, end)
    snapshot = await repository.read_snapshot(db, parameters)
    engagement, trends = summarize(snapshot["engagement"], start, weeks)
    return SchoolInsights(
        school_id=school.wriveted_identifier,
        school_name=school.name,
        start_date=start.date(),
        end_date=end.date(),
        generated_at=now,
        engagement=engagement,
        collection=snapshot["collection"],
        trends=trends,
        interests=snapshot["interests"],
    )

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from app.api.dependencies.async_db_dep import get_async_session
from app.api.dependencies.security import (
    get_current_active_superuser_or_backend_service_account,
)
from app.models.cms import ConversationSession, SessionStatus
from app.models.collection_item_activity import (
    CollectionItemActivity,
    CollectionItemReadStatus,
)
from app.models.educator import Educator
from app.models.school import School, SchoolState
from app.models.student import Student
from app.models.user import User
from app.schemas.kpis import (
    EngagementSummary,
    KpiOverview,
    KpiTrends,
    SchoolFunnel,
    TrendPoint,
    UserCounts,
)

logger = get_logger()

router = APIRouter(
    tags=["KPIs"],
    dependencies=[Depends(get_current_active_superuser_or_backend_service_account)],
)


def _enum_value(member) -> str:
    return member.value if hasattr(member, "value") else str(member)


@router.get("/kpis/overview", response_model=KpiOverview)
async def get_kpi_overview(session: AsyncSession = Depends(get_async_session)):
    """Headline KPIs: school funnel, user breakdown, and engagement totals.

    Admin-only. Backed by grouped counts over indexed columns; cache on the
    client (these change slowly).
    """
    # School funnel
    school_rows = (
        await session.execute(
            select(School.state, func.count(School.id)).group_by(School.state)
        )
    ).all()
    school_counts = {_enum_value(state): count for state, count in school_rows}
    schools = SchoolFunnel(
        active=school_counts.get(SchoolState.ACTIVE.value, 0),
        pending=school_counts.get(SchoolState.PENDING.value, 0),
        inactive=school_counts.get(SchoolState.INACTIVE.value, 0),
        total=sum(school_counts.values()),
    )

    # Users by type
    user_rows = (
        await session.execute(
            select(User.type, func.count(User.id)).group_by(User.type)
        )
    ).all()
    by_type = {_enum_value(user_type): count for user_type, count in user_rows}
    users = UserCounts(by_type=by_type, total=sum(by_type.values()))

    # Engagement: conversation sessions by status
    session_rows = (
        await session.execute(
            select(
                ConversationSession.status, func.count(ConversationSession.id)
            ).group_by(ConversationSession.status)
        )
    ).all()
    session_counts = {_enum_value(status): count for status, count in session_rows}
    completed = session_counts.get(SessionStatus.COMPLETED.value, 0)
    abandoned = session_counts.get(SessionStatus.ABANDONED.value, 0)
    active = session_counts.get(SessionStatus.ACTIVE.value, 0)
    finished = completed + abandoned
    completion_rate = round(completed / finished, 4) if finished else None

    books_read = (
        await session.execute(
            select(func.count(CollectionItemActivity.id)).where(
                CollectionItemActivity.status == CollectionItemReadStatus.READ
            )
        )
    ).scalar_one()

    engagement = EngagementSummary(
        sessions_total=sum(session_counts.values()),
        sessions_active=active,
        sessions_completed=completed,
        sessions_abandoned=abandoned,
        completion_rate=completion_rate,
        books_read=books_read,
    )

    return KpiOverview(schools=schools, users=users, engagement=engagement)


async def _weekly_counts(
    session: AsyncSession, timestamp_column, cutoff: datetime, *extra_filters
) -> dict:
    """Return {week_start_date: count} bucketed by ISO week (Postgres date_trunc)."""
    week = func.date_trunc("week", timestamp_column).label("week")
    query = select(week, func.count()).where(timestamp_column >= cutoff).group_by(week)
    for clause in extra_filters:
        query = query.where(clause)
    rows = (await session.execute(query)).all()
    return {row[0].date(): row[1] for row in rows}


@router.get("/kpis/trends", response_model=KpiTrends)
async def get_kpi_trends(
    weeks: int = Query(12, ge=1, le=52),
    session: AsyncSession = Depends(get_async_session),
):
    """Weekly trend buckets (zero-filled) for the last `weeks` ISO weeks.

    Tracks new schools, new students, new educators, conversation sessions
    started, and books marked read.
    """
    today = datetime.utcnow().date()
    # Monday of the current ISO week, then walk back to the first bucket.
    current_monday = today - timedelta(days=today.weekday())
    first_monday = current_monday - timedelta(weeks=weeks - 1)
    cutoff = datetime.combine(first_monday, datetime.min.time())

    schools = await _weekly_counts(session, School.created_at, cutoff)
    students = await _weekly_counts(session, Student.created_at, cutoff)
    educators = await _weekly_counts(session, Educator.created_at, cutoff)
    sessions = await _weekly_counts(session, ConversationSession.started_at, cutoff)
    books_read = await _weekly_counts(
        session,
        CollectionItemActivity.timestamp,
        cutoff,
        CollectionItemActivity.status == CollectionItemReadStatus.READ,
    )

    points: list[TrendPoint] = []
    for i in range(weeks):
        wk = first_monday + timedelta(weeks=i)
        points.append(
            TrendPoint(
                week=wk,
                new_schools=schools.get(wk, 0),
                new_students=students.get(wk, 0),
                new_educators=educators.get(wk, 0),
                conversation_sessions=sessions.get(wk, 0),
                books_read=books_read.get(wk, 0),
            )
        )

    return KpiTrends(weeks=weeks, points=points)

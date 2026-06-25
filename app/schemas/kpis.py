from datetime import date

from pydantic import BaseModel


class SchoolFunnel(BaseModel):
    active: int
    pending: int
    inactive: int
    total: int


class UserCounts(BaseModel):
    """Counts keyed by UserAccountType value (student, educator, parent, ...)."""

    by_type: dict[str, int]
    total: int


class EngagementSummary(BaseModel):
    sessions_total: int
    sessions_active: int
    sessions_completed: int
    sessions_abandoned: int
    # completed / (completed + abandoned); null when no finished sessions yet.
    completion_rate: float | None
    books_read: int


class KpiOverview(BaseModel):
    schools: SchoolFunnel
    users: UserCounts
    engagement: EngagementSummary


class TrendPoint(BaseModel):
    week: date
    new_schools: int
    new_students: int
    new_educators: int
    conversation_sessions: int
    books_read: int


class KpiTrends(BaseModel):
    weeks: int
    points: list[TrendPoint]

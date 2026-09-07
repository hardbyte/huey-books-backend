from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel


class CollectionHealth(BaseModel):
    works: int
    labelled: int
    awaiting_review: int
    missing_age: int
    unmatched_items: int


class Engagement(BaseModel):
    sessions: int | None
    reached_recommendations: int | None
    recommendation_rate: float | None
    feedback_sessions: int | None
    liked: int | None
    disliked: int | None
    already_read: int | None


class SchoolTrend(BaseModel):
    week: date
    sessions: int


class InterestOpportunity(BaseModel):
    name: str
    sessions: int
    labelled_works: int


class SchoolInsights(BaseModel):
    school_id: UUID
    school_name: str
    start_date: date
    end_date: date
    generated_at: datetime
    privacy_threshold: int = 5
    engagement: Engagement
    collection: CollectionHealth
    trends: list[SchoolTrend]
    interests: list[InterestOpportunity]

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.labelset import LabelOrigin, RecommendStatus


class LabelSetReviewIn(BaseModel):
    """Input schema for submitting or updating a review."""

    hue_primary_key: Optional[str] = None
    hue_secondary_key: Optional[str] = None
    hue_tertiary_key: Optional[str] = None
    min_age: Optional[int] = None
    max_age: Optional[int] = None
    reading_ability_key: Optional[str] = None
    recommend_status: Optional[RecommendStatus] = None
    notes: Optional[str] = None
    confirmed_existing: Optional[bool] = None


class LabelSetReviewDetail(BaseModel):
    """Output schema for a review."""

    id: int
    labelset_id: int
    reviewer_user_id: UUID
    reviewer_name: Optional[str] = None

    hue_primary_key: Optional[str] = None
    hue_secondary_key: Optional[str] = None
    hue_tertiary_key: Optional[str] = None
    min_age: Optional[int] = None
    max_age: Optional[int] = None
    reading_ability_key: Optional[str] = None
    recommend_status: Optional[RecommendStatus] = None
    notes: Optional[str] = None
    confirmed_existing: Optional[bool] = None

    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ReviewQueueItem(BaseModel):
    """An item in the review queue — a work with labelset + review metadata."""

    work_id: int
    title: str
    subtitle: str | None = None
    leading_article: str | None = None
    authors: list[str]
    labelset_id: int | None = None
    hue_primary_key: str | None = None
    hue_origin: LabelOrigin | None = None
    checked: bool | None = None
    min_age: int | None = None
    max_age: int | None = None
    recommend_status: RecommendStatus | None = None
    school_count: int = 0
    collection_frequency: int = 0
    review_count: int = 0
    reviewer_names: list[str] = []


class ReviewerStat(BaseModel):
    user_id: UUID
    name: str
    review_count: int


class ReviewStats(BaseModel):
    total_works: int
    works_with_labelset: int
    works_checked: int
    works_unchecked: int
    works_human_hued: int
    works_ai_hued: int
    works_no_hue: int
    total_reviews: int
    top_reviewers: list[ReviewerStat]

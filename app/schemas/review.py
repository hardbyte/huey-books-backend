from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.labelset import LabelOrigin, RecommendStatus
from app.schemas.ai_assistance import AIAssistance
from app.schemas.recommendations import HueKeys, ReadingAbilityKey


class LabelSetReviewIn(BaseModel):
    """Input schema for submitting or updating a review."""

    hue_primary_key: HueKeys | None = None
    hue_secondary_key: HueKeys | None = None
    hue_tertiary_key: HueKeys | None = None
    min_age: int | None = Field(default=None, ge=0, le=100)
    max_age: int | None = Field(default=None, ge=0, le=100)
    reading_ability_key: ReadingAbilityKey | None = None
    expected_reading_ability_keys: list[ReadingAbilityKey] | None = Field(
        default=None, min_length=1, max_length=20
    )
    recommend_status: Optional[RecommendStatus] = None
    notes: Optional[str] = None
    confirmed_existing: Optional[bool] = None
    ai_assistance: AIAssistance | None = None

    @model_validator(mode="after")
    def valid_labels(self):
        if self.expected_reading_ability_keys is not None:
            if not self.confirmed_existing or self.reading_ability_key is not None:
                raise ValueError(
                    "An existing reading-level snapshot is only valid for unchanged confirmation without a replacement level"
                )
            if len(set(self.expected_reading_ability_keys)) != len(
                self.expected_reading_ability_keys
            ):
                raise ValueError("Existing reading levels must be distinct")
        if (
            self.min_age is not None
            and self.max_age is not None
            and self.min_age > self.max_age
        ):
            raise ValueError("Minimum age must not exceed maximum age")
        hues = [
            key
            for key in (
                self.hue_primary_key,
                self.hue_secondary_key,
                self.hue_tertiary_key,
            )
            if key
        ]
        if len(hues) != len(set(hues)):
            raise ValueError("Choose distinct hues")
        if (
            self.hue_secondary_key or self.hue_tertiary_key
        ) and not self.hue_primary_key:
            raise ValueError("Choose a primary hue first")
        if self.hue_tertiary_key and not self.hue_secondary_key:
            raise ValueError("Choose a secondary hue before a tertiary hue")
        return self


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
    expected_reading_ability_keys: list[str] | None = None
    recommend_status: Optional[RecommendStatus] = None
    notes: Optional[str] = None
    confirmed_existing: Optional[bool] = None
    ai_assistance: AIAssistance | None = None

    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ReviewQueueItem(BaseModel):
    """An item in the review queue — a work with labelset + review metadata."""

    work_id: int
    title: str
    subtitle: str | None = None
    leading_article: str | None = None
    cover_url: str | None = None
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

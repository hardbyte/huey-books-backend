from datetime import date, datetime
from enum import IntEnum
from typing import Final, Literal
from uuid import UUID

from pydantic import AliasChoices, BaseModel, Field, computed_field

PRIVACY_THRESHOLD: Final = 5


class InsightsWeeks(IntEnum):
    FOUR = 4
    TWELVE = 12
    TWENTY_SIX = 26


class InsightsSemantics(BaseModel):
    timezone: Literal["UTC"] = "UTC"
    end_date_exclusive: Literal[True] = True
    activity_basis: Literal["session_started_at"] = "session_started_at"
    outcomes_basis: Literal["latest_available"] = "latest_available"
    feedback_basis: Literal["latest_recorded_submission"] = "latest_recorded_submission"
    interests_basis: Literal["current_session_state"] = "current_session_state"
    collection_basis: Literal["current_snapshot"] = "current_snapshot"
    feedback_unit: Literal["isbn_choices_per_session"] = "isbn_choices_per_session"


class InsightsAvailability(BaseModel):
    sessions: Literal["available", "privacy_suppressed"]
    reached_recommendations: Literal["available", "privacy_suppressed"]
    recommendation_rate: Literal["available", "privacy_suppressed", "no_sessions"]
    feedback_sessions: Literal["available", "privacy_suppressed"]
    feedback_choices: Literal["available", "privacy_suppressed", "unverified_history"]
    trends: Literal["available", "privacy_suppressed"]
    interests: Literal["privacy_filtered"] = Field(
        default="privacy_filtered",
        description="Only safe groups are returned; an empty list does not distinguish missing from suppressed groups.",
    )


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
    school_uuid: UUID = Field(validation_alias=AliasChoices("school_uuid", "school_id"))
    school_name: str
    start_date: date
    end_date: date
    generated_at: datetime
    semantics: InsightsSemantics = Field(default_factory=InsightsSemantics)
    availability: InsightsAvailability
    privacy_threshold: int = PRIVACY_THRESHOLD
    engagement: Engagement
    collection: CollectionHealth
    trends: list[SchoolTrend]
    interests: list[InterestOpportunity]

    @computed_field(json_schema_extra={"deprecated": True})
    @property
    def school_id(self) -> UUID:
        return self.school_uuid

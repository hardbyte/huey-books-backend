from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID, uuid4

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, StringConstraints
from typing_extensions import Annotated

from app.models import SchoolState
from app.models.school import SchoolBookbotType
from app.schemas.collection import CollectionBrief
from app.schemas.country import CountryDetail

# pylint: disable=unused-import
from app.schemas.school_identity import SchoolIdentity
from app.schemas.subscription import SubscriptionBrief, SubscriptionDetail
from app.schemas.users import UserBrief


class SchoolLocation(BaseModel):
    # All fields are optional and lat/long are coerced from numbers: location
    # data comes from varied sources and stores coordinates numerically or as
    # strings. (Pydantic v2 no longer coerces numbers to str implicitly, so a
    # numeric lat/long would otherwise fail response serialization.)
    model_config = ConfigDict(coerce_numbers_to_str=True)

    suburb: Optional[str] = None
    state: Optional[str] = None
    postcode: Optional[str] = None
    geolocation: Optional[str] = None
    lat: Optional[str] = None
    long: Optional[str] = None


class SchoolInfo(BaseModel):
    location: SchoolLocation
    type: Optional[str] = None
    sector: Optional[str] = None
    URL: Optional[str] = None
    status: Optional[str] = None
    age_id: Optional[str] = None
    experiments: Optional[dict[str, bool]] = None
    terms_acceptance: Optional[dict[str, Any]] = None


def normalize_school_info(
    info: Optional[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    """Coerce a School.info dict's location coordinates to the canonical shape.

    School.info is untyped JSONB written by several sources that store the
    location block's coordinates inconsistently (lat/long as numbers or as
    strings). Coordinates are coerced to strings via SchoolLocation so stored
    data matches the serialized response shape. Every other info key, and any
    unmodelled key inside location, is preserved.
    """
    if not isinstance(info, dict):
        return info
    location = info.get("location")
    if not isinstance(location, dict):
        return info
    canonical_location = SchoolLocation.model_validate(location).model_dump()
    merged_location = {**location, **canonical_location}
    return {**info, "location": merged_location}


class CompGrantRequest(BaseModel):
    days: int = Field(90, ge=1, le=3650)
    idempotency_key: str = Field(
        default_factory=lambda: str(uuid4()), min_length=1, max_length=128
    )
    reason: str | None = Field(default=None, max_length=500)
    campaign_id: str | None = Field(default=None, max_length=128)


class CompGrantResponse(BaseModel):
    outcome: Literal["granted", "extended", "unchanged"]
    state: SchoolState
    access_until: datetime
    idempotent_replay: bool


class SchoolBrief(SchoolIdentity):
    name: str
    state: SchoolState | None = None
    subscription: SubscriptionBrief | None = None
    collection: CollectionBrief | None = None


class SchoolSelectorOption(SchoolBrief):
    info: SchoolInfo
    admins: list[UserBrief]


class SchoolBookbotInfo(BaseModel):
    wriveted_identifier: UUID
    name: str
    state: SchoolState
    bookbot_type: SchoolBookbotType

    model_config = ConfigDict(from_attributes=True)


class BookListID(BaseModel):
    id: UUID
    name: str
    model_config = ConfigDict(from_attributes=True)


class SchoolDetail(SchoolBrief):
    country: CountryDetail
    info: Optional[SchoolInfo] = None

    admins: list[UserBrief]
    lms_type: str
    bookbot_type: SchoolBookbotType

    created_at: datetime
    updated_at: datetime

    student_domain: Optional[AnyHttpUrl] = None
    teacher_domain: Optional[AnyHttpUrl] = None

    booklists: list[BookListID]

    subscription: SubscriptionDetail | None = None


class SchoolCreateIn(BaseModel):
    name: str
    country_code: Annotated[str, StringConstraints(min_length=3, max_length=3)]
    official_identifier: Optional[str] = None
    bookbot_type: Optional[SchoolBookbotType] = None
    lms_type: Optional[str] = None
    info: SchoolInfo
    student_domain: Optional[AnyHttpUrl] = None
    teacher_domain: Optional[AnyHttpUrl] = None


# Note can't change the country code or official identifier
class SchoolUpdateIn(BaseModel):
    name: Optional[str] = None
    info: Optional[Any] = None
    student_domain: Optional[AnyHttpUrl] = None
    teacher_domain: Optional[AnyHttpUrl] = None


class SchoolPatchOptions(BaseModel):
    status: Optional[SchoolState] = None
    bookbot_type: Optional[SchoolBookbotType] = None
    lms_type: Optional[str] = None
    name: Optional[str] = None
    info: Optional[Any] = None
    student_domain: Optional[AnyHttpUrl] = None
    teacher_domain: Optional[AnyHttpUrl] = None

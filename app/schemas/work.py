from typing import Annotated, List, Optional

from pydantic import BaseModel, BeforeValidator, ConfigDict, field_validator

from app.models.work import WorkType
from app.schemas.author import AuthorBrief, AuthorCreateIn
from app.schemas.edition import EditionBrief, Genre
from app.schemas.labelset import LabelSetBasic, LabelSetCreateIn, LabelSetDetail


class WorkInfo(BaseModel):
    genres: list[Genre] = []
    other: dict = {}

    @field_validator("genres", mode="before")
    @classmethod
    def genres_not_none(cls, v):
        return v or []


class WorkBrief(BaseModel):
    id: Annotated[str, BeforeValidator(str)]
    type: Optional[WorkType] = None

    leading_article: str | None = None
    title: str
    subtitle: str | None = None

    authors: List[AuthorBrief]

    model_config = ConfigDict(from_attributes=True)


class WorkEnriched(WorkBrief):
    labelset: LabelSetBasic | None = None
    cover_url: str | None = None


class WorkDetail(WorkEnriched):
    editions: List[EditionBrief]
    labelset: Optional[LabelSetDetail] = None
    info: Optional[WorkInfo] = None


class WorkCreateIn(BaseModel):
    type: WorkType

    leading_article: Optional[str] = None
    title: str
    subtitle: Optional[str] = None

    authors: List[AuthorCreateIn]

    series_name: Optional[str] = None
    series_number: Optional[int] = None

    info: Optional[dict] = None


class WorkCreateWithEditionsIn(BaseModel):
    type: WorkType = WorkType.BOOK
    leading_article: Optional[str] = None
    title: str
    subtitle: Optional[str] = None
    authors: List[AuthorCreateIn | int]
    editions: list[str]
    info: Optional[dict] = None


class WorkUpdateIn(BaseModel):
    leading_article: str | None = None
    title: str | None = None
    subtitle: str | None = None

    labelset: LabelSetCreateIn | None = None

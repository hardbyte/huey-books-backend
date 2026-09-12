from datetime import datetime
from typing import Annotated, Optional

from pydantic import (
    AfterValidator,
    AnyHttpUrl,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    field_validator,
)

from app.schemas import CaseInsensitiveStringEnum
from app.schemas.author import AuthorBrief, AuthorCreateIn, ContributorBase
from app.schemas.illustrator import IllustratorBrief, IllustratorCreateIn
from app.schemas.image_url import ImageUrl, validate_image_url_or_base64_string
from app.schemas.labelset import LabelSetCreateIn
from app.schemas.link import LinkBrief


class Genre(BaseModel):
    class GenreSource(CaseInsensitiveStringEnum):
        BISAC = "BISAC"
        BIC = "BIC"
        THEMA = "THEMA"
        LOCSH = "LOCSH"
        HUMAN = "HUMAN"
        VERTEXAI = "VERTEXAI"
        OTHER = "OTHER"

    name: str
    source: GenreSource
    code: str | None = None


class EditionInfo(BaseModel):
    # nielsen fields
    pages: Annotated[
        str | None, BeforeValidator(lambda v: v if v is None else str(v))
    ] = None  # PAGNUM
    summary_short: str | None = None  # AUSFSD
    summary_long: str | None = None  # AUSFLD
    genres: list[Genre] = []  # BISACT/C{n}, BIC2ST/C{n}, THEMAST/C{n}, LOCSH{n}
    bic_qualifiers: list[str] = []  # BIC2QC{n}
    thema_qualifiers: list[str] = []  # THEMAQC{n}
    keywords: str | None = None  # KEYWORDS (comes as a delimited string, not a list)
    prodct: str | None = None  # PRODCT
    prodcc: str | None = None  # PRODCC
    cbmctext: str | None = None  # CBMCTEXT
    cbmccode: str | None = None  # CBMCCODE
    interest_age: str | None = None  # IA
    reading_age: str | None = None  # RA
    country: str | None = None  # COP
    medium_tags: list[str] = []  # PFCT, PCTCT{n}
    image_flag: bool | None = None  # IMAGFLAG

    links: list[LinkBrief] = []
    other: dict | None = None


class EditionBrief(BaseModel):
    leading_article: Optional[str] = None
    title: Optional[str] = None
    subtitle: Optional[str] = None
    cover_url: Optional[AnyHttpUrl] = None
    work_id: Annotated[
        Optional[str], BeforeValidator(lambda x: x if x is None else str(x))
    ] = None
    isbn: str
    model_config = ConfigDict(from_attributes=True)

    @field_validator("cover_url", mode="before")
    @classmethod
    def normalize_cover_url(cls, value):
        return value.strip() or None if isinstance(value, str) else value


class EditionDetail(EditionBrief):
    series_name: Optional[str] = None
    series_number: Optional[int] = None

    authors: list[AuthorBrief]
    illustrators: list[IllustratorBrief]

    date_published: Optional[int] = None
    info: Optional[EditionInfo] = None

    @field_validator("authors", "illustrators", mode="before")
    @classmethod
    def contributors_not_none(cls, v):
        return v or []


class EditionCreateIn(BaseModel):
    isbn: str
    other_isbns: Optional[list[str]] = None

    work_id: int | None = None

    leading_article: Optional[str] = None
    title: Optional[str] = None
    subtitle: Optional[str] = None

    series_name: Optional[str] = None
    series_number: Optional[int] = None

    authors: Optional[list[AuthorCreateIn]] = None
    illustrators: Optional[list[IllustratorCreateIn]] = None

    cover_url: Annotated[
        str | None,
        AfterValidator(
            lambda v: validate_image_url_or_base64_string(v, field_name="cover_url")
        ),
    ] = None

    date_published: Optional[int] = None

    labelset: Optional[LabelSetCreateIn] = None
    info: Optional[EditionInfo] = None

    hydrated: bool = False


class EditionUpdateIn(BaseModel):
    leading_article: str | None = None
    edition_title: str | None = None
    edition_subtitle: str | None = None

    date_published: str | None = None

    illustrators: list[ContributorBase | int] | None = None

    cover_url: ImageUrl | None = None

    work_id: int | None = None

    info: EditionInfo | None = None

    hydrated_at: datetime | None = None


class KnownAndTaggedEditionCounts(BaseModel):
    num_provided: int
    num_valid: int
    num_known: int
    num_fully_tagged: int

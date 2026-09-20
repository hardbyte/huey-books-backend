from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from structlog import get_logger
from typing_extensions import Annotated

from app.models.collection_item_activity import CollectionItemReadStatus
from app.schemas import CaseInsensitiveStringEnum
from app.schemas.edition import EditionBrief
from app.schemas.image_url import ImageUrl
from app.schemas.pagination import PaginatedResponse
from app.schemas.work import WorkBrief

logger = get_logger()


class CollectionBrief(BaseModel):
    id: UUID
    name: str
    book_count: int
    school_id: UUID | None = None
    user_id: UUID | None = None
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class CollectionInfo(BaseModel):
    """
    Count holdings in each state in a collection.

    Note this doesn't count additional copies of the same book.
    """

    total_editions: int = Field(
        ...,
        description="Count of holdings in this collection, including unresolved items; additional copies are not counted",
    )

    hydrated: int = Field(
        ...,
        description="Count of unique editions for which Wriveted has basic metadata",
    )
    hydrated_and_labeled: int = Field(
        ..., description="Count of unique editions for which Wriveted has labelled"
    )
    recommendable: int = Field(
        ..., description="Count of unique editions labelled and marked as recommendable"
    )


class CollectionItemFeedback(BaseModel):
    """
    Feedback about a collection item.
    This is also used to update the feedback for a collection item.
    """

    emojis: list[str] | None = None
    descriptor: str | None = None


class CollectionItemInfo(BaseModel):
    cover_image: Optional[ImageUrl] = None
    title: str | None = None
    author: str | None = None

    feedback: CollectionItemFeedback | None = None

    other: dict[str, Any] | None = None


class CollectionItemInfoCreateIn(CollectionItemInfo):
    cover_image: Optional[ImageUrl] = None
    model_config = ConfigDict(
        str_max_length=int((2**19) * 1.5), validate_assignment=True
    )


class CoverImageUpdateIn(CollectionItemInfoCreateIn):
    collection_id: UUID | None = None
    edition_isbn: str


class CollectionItemBase(BaseModel):
    edition_isbn: str | None = None
    info: CollectionItemInfoCreateIn | None = None
    copies_total: Optional[Annotated[int, Field(ge=0)]] = None
    copies_available: Optional[Annotated[int, Field(ge=0)]] = None


class CollectionItemCreateIn(CollectionItemBase):
    pass


class CollectionItemAndStatusCreateIn(CollectionItemCreateIn):
    read_status: CollectionItemReadStatus | None = None
    reader_id: UUID | None = None

    @model_validator(mode="before")
    @classmethod
    def _validate_logic(cls, values: dict):
        if not values:
            return

        # if providing one, must provide both
        if (
            values.get("read_status")
            and not values.get("reader_id")
            or (values.get("reader_id") and not values.get("read_status"))
        ):
            raise ValueError(
                "Must provide reader_id when providing read_status, and vice versa"
            )

        return values


class CollectionCreateIn(BaseModel):
    name: str

    school_id: UUID | None = None
    user_id: UUID | None = None

    info: dict[str, Any] | None = None
    items: list[CollectionItemCreateIn] | None = None

    @model_validator(mode="before")
    @classmethod
    def _validate_relationships(cls, values: dict):
        school_id = values.get("school_id")
        user_id = values.get("user_id")
        if not school_id and not user_id:
            raise ValueError("Must provide either school_id or user_id")
        if school_id and user_id:
            raise ValueError("Must provide only one of school_id or user_id")
        return values


class CollectionItemDetail(BaseModel):
    id: int
    work: Optional[WorkBrief] = None
    edition: EditionBrief | None = None

    copies_total: Optional[Annotated[int, Field(ge=0)]] = None
    copies_available: Optional[Annotated[int, Field(ge=0)]] = None

    info: CollectionItemInfo | None = None
    model_config = ConfigDict(from_attributes=True)


class CollectionItemsResponse(PaginatedResponse):
    data: list[CollectionItemDetail]


class CollectionUpdateType(CaseInsensitiveStringEnum):
    ADD = "add"
    REMOVE = "remove"
    UPDATE = "update"


class CollectionItemUpdate(CollectionItemCreateIn):
    action: CollectionUpdateType = CollectionUpdateType.ADD
    id: int | None = Field(
        None,
        description="Item id or edition_isbn required if action is `update` or `remove`",
    )
    model_config = ConfigDict(from_attributes=True)


class CollectionUpdateIn(BaseModel):
    name: str | None = None
    info: dict[str, Any] | None = None


class CollectionAndItemsUpdateIn(CollectionUpdateIn):
    items: list[CollectionItemUpdate] | None = None


class CollectionUpdateSummaryResponse(BaseModel):
    msg: str
    collection_size: int


class CollectionItemActivityBase(BaseModel):
    collection_item_id: int
    reader_id: UUID
    status: CollectionItemReadStatus
    model_config = ConfigDict(from_attributes=True)


class CollectionItemActivityBrief(CollectionItemActivityBase):
    timestamp: datetime

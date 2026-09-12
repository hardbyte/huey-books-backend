from datetime import datetime
from typing import Optional

from pydantic import UUID4, AnyHttpUrl, BaseModel, ConfigDict, Field

from app.models.booklist import ListSharingType, ListType
from app.schemas import CaseInsensitiveStringEnum
from app.schemas.edition import EditionDetail
from app.schemas.image_url import ImageUrl
from app.schemas.pagination import PaginatedResponse
from app.schemas.school_identity import SchoolWrivetedIdentity
from app.schemas.users.user_identity import UserIdentity
from app.schemas.work import WorkEnriched


class BookFeedbackChoice(CaseInsensitiveStringEnum):
    GOOD = "GOOD"
    BAD = "BAD"
    READ_GOOD = "READ_GOOD"
    READ_BAD = "READ_BAD"


class BookListItemInfo(BaseModel):
    edition: Optional[str] = Field(None, description="ISBN")
    note: Optional[str] = Field(None, description="Note from the booklist creator")
    feedback: BookFeedbackChoice | None = None


class BookListItemBase(BaseModel):
    order_id: int
    work_id: int
    info: Optional[BookListItemInfo] = None


class BookListItemDetail(BookListItemBase):
    # id: UUID
    work: WorkEnriched
    model_config = ConfigDict(from_attributes=True)


class AlternativeBookCover(BaseModel):
    url: AnyHttpUrl
    source_isbn: str


class BookListItemEnriched(BookListItemDetail):
    edition: EditionDetail
    observation_receipt: str | None = None
    alternative_cover: AlternativeBookCover | None = Field(
        default=None,
        description="Cover from another edition of the same work. Display 'Cover may differ' when using it.",
    )


class BookListItemCreateIn(BookListItemBase):
    order_id: Optional[int] = None


class BookListBase(BaseModel):
    id: UUID4
    name: str
    type: ListType
    book_count: int | None = None
    model_config = ConfigDict(from_attributes=True)


class BookListOptionalInfo(BaseModel):
    description: Optional[str] = None
    image_url: Optional[str] = None
    subheading: Optional[str] = None
    colour: Optional[str] = None


class BookListOptionalInfoCreateIn(BookListOptionalInfo):
    image_url: ImageUrl | None = None


class BookListCreateIn(BaseModel):
    name: str
    type: ListType
    slug: str | None = None
    sharing: ListSharingType | None = None

    school_id: str | None = Field(None, coerce_numbers_to_str=True)
    user_id: str | None = None

    info: BookListOptionalInfo | None = None
    items: list[BookListItemCreateIn] | None = None


class ItemUpdateType(CaseInsensitiveStringEnum):
    ADD = "add"
    REMOVE = "remove"
    UPDATE = "update"


class BookListItemUpdateIn(BaseModel):
    action: ItemUpdateType
    work_id: int
    order_id: Optional[int] = None
    info: Optional[BookListItemInfo] = None


class BookListUpdateIn(BaseModel):
    name: Optional[str] = None
    type: Optional[ListType] = None
    sharing: Optional[ListSharingType] = None
    slug: Optional[str] = None
    info: Optional[BookListOptionalInfo] = None
    items: Optional[list[BookListItemUpdateIn]] = None


class BookListBrief(BookListBase):
    created_at: datetime
    updated_at: datetime
    user: Optional[UserIdentity] = None
    school: Optional[SchoolWrivetedIdentity] = None
    sharing: Optional[ListSharingType] = None
    slug: Optional[str] = None
    info: Optional[BookListOptionalInfo] = None


class BookListsResponse(PaginatedResponse):
    data: list[BookListBrief]


class BookListDetail(PaginatedResponse, BookListBrief):
    data: list[BookListItemDetail]


class BookListDetailEnriched(BookListDetail):
    data: list[BookListItemEnriched]

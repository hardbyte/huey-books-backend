from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class OrganisationCreate(StrictInput):
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["school", "public_library", "other"]


class OrganisationEntitlements(BaseModel):
    multiple_libraries: bool
    library_limit: int
    library_count: int
    reason: Literal["paid_subscription", "paid_subscription_required"]


class LibraryCreate(StrictInput):
    name: str = Field(min_length=1, max_length=200)
    country_code: str = Field(pattern="^[A-Z]{3}$")
    collection_name: str = Field(
        default="Main collection", min_length=1, max_length=200
    )


class CollectionCreate(StrictInput):
    name: str = Field(min_length=1, max_length=200)


class LibraryDetailsUpdate(StrictInput):
    name: str = Field(min_length=1, max_length=200)
    expected_name: str


class MemberChange(StrictInput):
    role: Literal["manager", "reviewer", "cataloguer"] = "manager"


class MemberByEmail(MemberChange):
    email: EmailStr


class ImportItem(StrictInput):
    edition_isbn: str = Field(min_length=10, max_length=32)
    title: str | None = Field(default=None, max_length=512)
    copies_total: int = Field(default=1, ge=0, le=100000)
    expected_copies_total: int | None = Field(
        default=None,
        ge=0,
        le=100000,
        description="Omit for an unconditional update; null requires a new holding; an integer must match the current count.",
    )

    @field_validator("edition_isbn")
    @classmethod
    def normalize_isbn(cls, value: str) -> str:
        from app.services.editions import get_definitive_isbn

        try:
            return get_definitive_isbn(value)
        except (AssertionError, ValueError, TypeError) as exc:
            raise ValueError("Enter a valid ISBN-10 or ISBN-13") from exc


class CollectionImport(StrictInput):
    dry_run: bool = True
    items: list[ImportItem] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def unique_isbns(self):
        if len({item.edition_isbn for item in self.items}) != len(self.items):
            raise ValueError("Combine duplicate ISBN rows before importing")
        return self


class CollectionSummary(BaseModel):
    id: UUID
    name: str
    is_default: bool
    book_count: int


class LibrarySummary(BaseModel):
    library_uuid: UUID
    name: str
    organisation_uuid: UUID | None
    country_code: str | None
    capabilities: list[str]
    access_sources: list[str]
    collections: list[CollectionSummary]


class OrganisationSummary(BaseModel):
    id: UUID
    name: str
    kind: str
    can_manage: bool
    entitlements: OrganisationEntitlements


class OrganisationDetail(OrganisationSummary):
    libraries: list[LibrarySummary]


class OrganisationList(BaseModel):
    data: list[OrganisationSummary]

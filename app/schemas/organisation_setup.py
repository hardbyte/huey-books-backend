from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.schemas.organisation import OrganisationKind, StrictInput
from app.schemas.people import PersonInvite

# Codes let the wizard branch and localise. The message is a fallback for
# clients that do not know a code yet.
IneligibilityCode = Literal[
    "already_grouped",
    "no_paid_subscription",
    "multiple_paid_subscriptions",
    "subscription_already_owned",
]


class Ineligibility(BaseModel):
    code: IneligibilityCode
    message: str


class OrganisationSetupInput(StrictInput):
    request_id: UUID
    expected_library_name: str = Field(min_length=1, max_length=256)
    organisation_name: str = Field(min_length=1, max_length=200)
    organisation_kind: OrganisationKind = "school"
    new_library_name: str = Field(min_length=1, max_length=200)
    collection_name: str = Field(
        default="Main collection", min_length=1, max_length=200
    )
    # Colleagues from the existing library who will manage the organisation and
    # therefore both libraries. Nobody is included implicitly.
    manager_ids: list[UUID] = Field(min_length=1, max_length=50)
    # Optional librarian for the new library only.
    librarian: PersonInvite | None = None

    @model_validator(mode="after")
    def distinct_choices(self):
        if self.new_library_name.casefold() == self.expected_library_name.casefold():
            raise ValueError("Give the new library a different name")
        if len(self.manager_ids) != len(set(self.manager_ids)):
            raise ValueError("Choose each organisation manager once")
        return self


class OrganisationSetupPreview(BaseModel):
    library_uuid: UUID
    library_name: str
    organisation_uuid: UUID | None
    eligible: bool
    ineligibility: Ineligibility | None
    eligible_manager_count: int
    # The actor must include themselves unless they are platform staff.
    required_manager_id: UUID | None


class OrganisationSetupResult(BaseModel):
    organisation_uuid: UUID
    existing_library_uuid: UUID
    new_library_uuid: UUID

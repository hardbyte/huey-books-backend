from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

PeopleScope = Literal["libraries", "organisations"]
AccessSource = Literal["direct", "home", "organisation"]
PeopleRole = Literal["manager", "reviewer", "cataloguer", "educator", "school_admin"]


class PersonInvite(BaseModel):
    """Someone named by email who may or may not already have an account."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    email: EmailStr
    name: str = Field(min_length=1, max_length=200)


class PersonAdd(PersonInvite):
    role: PeopleRole


class PersonChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: PeopleRole
    expected_role: PeopleRole


class PersonAccess(BaseModel):
    source: AccessSource
    role: PeopleRole
    can_edit: bool
    can_remove: bool
    manage_url: str | None = None


class PersonBrief(BaseModel):
    """Identity only. Use this wherever people are listed for selection."""

    user_id: UUID
    name: str
    email: str | None


class PersonBriefPage(BaseModel):
    data: list[PersonBrief]
    total: int
    skip: int
    limit: int


class Person(PersonBrief):
    is_active: bool
    last_login_at: datetime | None
    access: list[PersonAccess]


class PeoplePage(BaseModel):
    name: str
    data: list[Person]
    total: int
    available_roles: list[PeopleRole]
    can_invite: bool

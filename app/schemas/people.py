from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

PeopleScope = Literal["libraries", "organisations"]
AccessSource = Literal["direct", "home", "organisation"]
PeopleRole = Literal["manager", "reviewer", "cataloguer", "educator", "school_admin"]


class PersonAdd(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    email: EmailStr
    name: str = Field(min_length=1, max_length=200)
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


class Person(BaseModel):
    user_id: UUID
    name: str
    email: str | None
    is_active: bool
    last_login_at: datetime | None
    access: list[PersonAccess]


class PeoplePage(BaseModel):
    name: str
    data: list[Person]
    total: int
    available_roles: list[PeopleRole]
    can_invite: bool

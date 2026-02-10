from __future__ import annotations

from pydantic import UUID4, BaseModel, EmailStr, model_validator

from app.models.user import UserAccountType
from app.schemas.users.huey_attributes import HueyAttributes
from app.schemas.users.user import UserInfo


class UserCreateAuth(BaseModel):
    """
    A schema to validate any initial `/auth/me` call that creates a user.
    """

    type: UserAccountType | None = UserAccountType.PUBLIC
    username: str | None = None
    first_name: str | None = None
    last_name_initial: str | None = None
    school_id: UUID4 | None = None
    class_join_code: UUID4 | None = None

    @model_validator(mode="after")
    def validate_user_creation(self):
        match self.type:
            case UserAccountType.STUDENT:
                assert self.school_id and self.class_join_code, (
                    "Student users must provide school_id and class_join_code."
                )
                self.name = f"{self.first_name} {self.last_name_initial}"

            case UserAccountType.EDUCATOR:
                assert self.first_name and self.last_name_initial and self.school_id, (
                    "Educator users must provide school_id."
                )
            case UserAccountType.SCHOOL_ADMIN:
                assert self.school_id, "SchoolAdmin users must provide school_id."
            case _:
                pass


class UserCreateIn(BaseModel):
    # all users
    name: str = None
    email: EmailStr | None = None
    info: UserInfo | None = None
    type: UserAccountType = UserAccountType.PUBLIC
    newsletter: bool = False

    # readers
    username: str | None = None
    first_name: str | None = None
    last_name_initial: str | None = None
    huey_attributes: HueyAttributes | None = None
    parent_id: UUID4 | None = None

    # students / educators
    school_id: UUID4 | int | None = None
    class_group_id: UUID4 | None = None

    # parents
    children: list[UserCreateIn] | None = None

    # subscription
    checkout_session_id: str | None = None

    @model_validator(mode="after")
    def validate_user_creation(self):
        # infer names from other fields if necessary
        name = self.name
        first_name = self.first_name
        last_name_initial = self.last_name_initial

        # Extract name from first name and initial
        if name is None and first_name and last_name_initial:
            self.name = f"{first_name} {last_name_initial}"

        # Extract first name and initial from name
        if name and self.type in {"public", "student"}:
            if not first_name:
                self.first_name = name.split()[0]
            if not last_name_initial:
                self.last_name_initial = name.split()[-1][0]

        # validate logic for supplied values vs. type
        match self.type:
            # case UserAccountType.PUBLIC:
            #     if not (values.get("first_name") and values.get("last_name_initial")):
            #         raise ValueError(
            #             "Public Readers must provide first_name and last_name_initial"
            #         )
            case UserAccountType.STUDENT:
                assert (
                    self.first_name
                    and self.last_name_initial
                    and self.school_id
                    and self.class_group_id
                ), (
                    "Student users must provide first_name, last_name_initial, school_id, and class_group_id."
                )
            case UserAccountType.EDUCATOR:
                assert self.first_name and self.last_name_initial and self.school_id, (
                    "Educator users must provide first_name, last_name_initial, and school_id."
                )
            case UserAccountType.SCHOOL_ADMIN:
                assert self.school_id, "SchoolAdmin users must provide school_id."
            case _:
                pass

        return self

from __future__ import annotations

from typing import Literal

from app.models.user import UserAccountType
from app.schemas.users.user import UserDetail, UsersSchool
from app.schemas.users.user_identity import UserBrief


class EducatorBrief(UserBrief):
    type: Literal[UserAccountType.EDUCATOR]
    school: UsersSchool | None
    # class_group: ClassGroupBrief | None


class EducatorDetail(UserDetail, EducatorBrief):
    type: Literal[UserAccountType.EDUCATOR]
    educator_info: dict | None = None

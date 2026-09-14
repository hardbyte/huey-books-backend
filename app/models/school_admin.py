import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import ForeignKey
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column

from app.models.educator import Educator
from app.models.user import UserAccountType


class SchoolAdmin(Educator):
    """
    A concrete School Admin user in a school context.
    The primary administrator / owner of a Huey school.
    """

    __tablename__ = "school_admins"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "educators.id", name="fk_school_admin_inherits_educator", ondelete="CASCADE"
        ),
        primary_key=True,
    )

    __mapper_args__ = {"polymorphic_identity": UserAccountType.SCHOOL_ADMIN}

    # class_history? other misc
    school_admin_info: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        MutableDict.as_mutable(JSONB),
        nullable=True,
        default={},  # type: ignore[arg-type]
    )

    def __repr__(self) -> str:
        active = "Active" if self.is_active else "Inactive"
        return f"<School Admin {self.name} - {self.school} - {active}>"

    async def get_principals(self) -> List[str]:
        principals = await super().get_principals()
        if self.school_id is not None:
            principals.append(f"schooladmin:{self.school_id}")
        return principals

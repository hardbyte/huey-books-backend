import uuid
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from fastapi_permissions import All, Allow  # type: ignore[import-untyped]
from sqlalchemy import JSON, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.user import User, UserAccountType

if TYPE_CHECKING:
    from app.models.school import School


class Educator(User):
    """
    A concrete Educator user in a school context.
    Could be a teacher, librarian, aid, principal, etc.
    """

    __tablename__ = "educators"  # type: ignore[assignment]

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_educator_inherits_user", ondelete="CASCADE"),
        primary_key=True,
    )

    __mapper_args__ = {"polymorphic_identity": UserAccountType.EDUCATOR}

    school_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("schools.id", name="fk_educator_school", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    school: Mapped[Optional["School"]] = relationship(
        "School", backref="educators", foreign_keys=[school_id]
    )

    # class_history? other misc
    educator_info: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        MutableDict.as_mutable(JSON),
        nullable=True,
        default={},  # type: ignore[arg-type]
    )

    def __repr__(self) -> str:
        active = "Active" if self.is_active else "Inactive"
        return f"<Educator {self.name} - {self.school} - {active}>"

    async def get_principals(self) -> List[str]:
        principals = await super().get_principals()

        if self.school_id is not None:
            principals.extend(["role:educator", f"educator:{self.school_id}"])

        return principals

    def __acl__(self) -> List[tuple[Any, str, Any]]:
        """defines who can do what to the instance
        the function returns a list containing tuples in the form of
        (Allow or Deny, principal identifier, permission name)
        If a role is not listed (like "role:user") the access will be
        automatically denied.
        (Deny, Everyone, All) is automatically appended at the end.
        """
        acl = super().__acl__()

        acl.extend(
            [
                (Allow, f"schooladmin:{self.school_id}", All),
                (Allow, f"educator:{self.school_id}", "read"),
            ]
        )

        return acl

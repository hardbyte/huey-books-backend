from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.educator import Educator
from app.models.school import School
from app.models.school_admin import SchoolAdmin
from app.models.user import User, UserAccountType
from app.schemas.users.user_update import UserUpdateIn
from app.services.workspace_errors import WorkspaceConflict, WorkspaceForbidden


def validate_account_access_change(
    session: Session, user: User, change: UserUpdateIn, *, is_staff: bool
) -> None:
    changes = change.model_dump(exclude_unset=True)
    adult_roles = {UserAccountType.EDUCATOR, UserAccountType.SCHOOL_ADMIN}
    if user.type not in adult_roles and change.type not in adult_roles:
        return
    protected = set(changes) & {"type", "school_id", "is_active"}
    if not protected:
        return
    school_id = getattr(user, "school_id", None)
    school = (
        session.scalar(select(School).where(School.id == school_id).with_for_update())
        if school_id
        else None
    )
    session.execute(select(User.id).where(User.id == user.id).with_for_update())
    session.refresh(user)
    if getattr(user, "school_id", None) != school_id:
        raise WorkspaceConflict(
            "School membership changed. Refresh before trying again."
        )
    changed = (
        ("type" in protected and change.type != user.type)
        or ("is_active" in protected and change.is_active != user.is_active)
        or (
            "school_id" in protected
            and change.school_id != (school.wriveted_identifier if school else None)
        )
    )
    if not changed:
        return
    if not is_staff:
        raise WorkspaceForbidden(
            "Manage school and library roles through People & access."
        )
    leaves_admin_role = (
        ("type" in protected and change.type != UserAccountType.SCHOOL_ADMIN)
        or ("is_active" in protected and change.is_active is not True)
        or (
            "school_id" in protected
            and change.school_id != (school.wriveted_identifier if school else None)
        )
    )
    if (
        school
        and user.type == UserAccountType.SCHOOL_ADMIN
        and user.is_active
        and leaves_admin_role
    ):
        count = session.scalar(
            select(func.count())
            .select_from(SchoolAdmin)
            .where(Educator.school_id == school.id, User.is_active.is_(True))
        )
        if count <= 1:
            raise WorkspaceConflict(
                "Add another school administrator before removing or changing the last administrator"
            )

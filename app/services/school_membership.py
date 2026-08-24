"""Shared school-membership helpers: promote a user to SchoolAdmin and bind them
to a school. Used by self-serve onboarding and by invite acceptance so the two
paths stay in lock-step (joined-table-inheritance juggling in one place)."""

from fastapi import HTTPException
from sqlalchemy import text
from starlette import status
from structlog import get_logger

from app.api.dependencies.async_db_dep import DBSessionDep
from app.models import School
from app.models.user import User, UserAccountType

logger = get_logger()

# Types that can be safely promoted to SchoolAdmin — any other type is rejected.
PROMOTABLE_TO_SCHOOL_ADMIN = {
    UserAccountType.PUBLIC,
    UserAccountType.STUDENT,
    UserAccountType.SUPPORTER,
}


async def promote_to_school_admin(db: DBSessionDep, user: User, school: School) -> None:
    """Promote a user to SchoolAdmin type and bind them to ``school``.

    Preserves identity (same user id). Rejects account types that must not be
    silently converted (e.g. an existing SCHOOL_ADMIN, WRIVETED staff, PARENT).
    """
    if user.type not in PROMOTABLE_TO_SCHOOL_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Your account type ({user.type.value}) cannot be converted to school admin. Contact support.",
        )

    user_id = user.id
    logger.info(
        "Promoting user to SchoolAdmin",
        user_id=str(user_id),
        old_type=user.type,
        school=school.name,
    )

    # Delete from the current type's subclass table.
    safe_type_table_map = {
        UserAccountType.PUBLIC: "public_readers",
        UserAccountType.STUDENT: "students",
        UserAccountType.SUPPORTER: "supporters",
    }
    subclass_table = safe_type_table_map.get(user.type)
    if subclass_table:
        await db.execute(
            text(f"DELETE FROM {subclass_table} WHERE id = :uid"), {"uid": user_id}
        )

    # PUBLIC/STUDENT inherit from Reader.
    await db.execute(text("DELETE FROM readers WHERE id = :uid"), {"uid": user_id})

    await db.execute(
        text("UPDATE users SET type = :new_type WHERE id = :uid"),
        {"new_type": UserAccountType.SCHOOL_ADMIN.value.upper(), "uid": user_id},
    )
    await bind_educator(db, user_id, school.id)
    await db.execute(
        text(
            "INSERT INTO school_admins (id) VALUES (:uid) ON CONFLICT (id) DO NOTHING"
        ),
        {"uid": user_id},
    )
    await db.flush()


async def bind_educator(db: DBSessionDep, user_id, school_id: int) -> None:
    """Bind (or move) a user's educator row to a school."""
    await db.execute(
        text(
            "INSERT INTO educators (id, school_id) VALUES (:uid, :school_id) "
            "ON CONFLICT (id) DO UPDATE SET school_id = :school_id"
        ),
        {"uid": user_id, "school_id": school_id},
    )

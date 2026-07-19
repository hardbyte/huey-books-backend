"""Shared family-onboarding logic.

Used by both the authenticated HTTP endpoint (``app.api.onboarding.onboard_family``)
and the chatflow internal handler
(``app.services.internal_api_handlers.handle_family_onboarding``) so that reader
profiles are created and linked to a parent account in exactly one place.
"""

from typing import Any, Mapping, Optional, Sequence

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status
from structlog import get_logger

from app.models.user import User, UserAccountType

logger = get_logger()

# Account types that can be safely promoted to a Parent account.
_PROMOTABLE_TO_PARENT = {
    UserAccountType.PUBLIC,
    UserAccountType.STUDENT,
    UserAccountType.SUPPORTER,
}

# Subclass tables to clear when converting a user to another account type.
_SUBCLASS_TABLE_BY_TYPE = {
    UserAccountType.PUBLIC: "public_readers",
    UserAccountType.STUDENT: "students",
    UserAccountType.SUPPORTER: "supporters",
}


async def _promote_to_parent(db: AsyncSession, user: User, parent_name: str) -> None:
    """Promote an authenticated user to a Parent account, preserving identity.

    Mirrors the promotion performed for school admins: remove the user from
    their current subclass table, flip ``users.type`` to PARENT and insert into
    the ``parents`` table. No-op when the user is already a parent.
    """
    if user.type not in _PROMOTABLE_TO_PARENT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Your account type ({user.type.value}) cannot be converted to "
                "a parent account. Contact support."
            ),
        )

    user_id = user.id

    subclass_table = _SUBCLASS_TABLE_BY_TYPE.get(user.type)
    if subclass_table:
        await db.execute(
            text(f"DELETE FROM {subclass_table} WHERE id = :uid"),
            {"uid": user_id},
        )

    # PUBLIC/STUDENT inherit from Reader — remove that row too.
    await db.execute(
        text("DELETE FROM readers WHERE id = :uid"),
        {"uid": user_id},
    )

    await db.execute(
        text("UPDATE users SET type = :new_type, name = :name WHERE id = :uid"),
        {
            "new_type": UserAccountType.PARENT.value.upper(),
            "name": parent_name,
            "uid": user_id,
        },
    )

    await db.execute(
        text("INSERT INTO parents (id) VALUES (:uid) ON CONFLICT (id) DO NOTHING"),
        {"uid": user_id},
    )

    await db.flush()


async def create_linked_family_readers(
    db: AsyncSession,
    *,
    user: User,
    parent_name: str,
    children: Sequence[Mapping[str, Any]],
) -> int:
    """Create child reader profiles linked to ``user`` as their parent.

    Promotes the user to a Parent account if needed, then creates one
    ``PublicReader`` per child with ``parent_id`` set. Flushes but does not
    commit — the caller controls the transaction boundary.

    ``children`` is a sequence of normalised mappings with keys ``name`` (str,
    required), ``age`` (Optional[int]), ``reading_ability`` (Optional[str]) and
    ``interests`` (Optional[list[str]]).

    Returns the number of readers created.
    """
    from app.models.public_reader import PublicReader

    if user.type != UserAccountType.PARENT:
        await _promote_to_parent(db, user, parent_name)

    children_created = 0
    for child in children:
        name = child.get("name")
        if not name:
            continue
        reader = PublicReader(
            name=name,
            first_name=name,
            parent_id=user.id,
            huey_attributes={
                "age": child.get("age"),
                "reading_ability": child.get("reading_ability"),
                "interests": child.get("interests"),
            },
        )
        db.add(reader)
        children_created += 1

    if children_created > 0:
        await db.flush()

    return children_created


def normalise_chatflow_child(child: Any) -> Optional[dict]:
    """Normalise a raw child dict from chatflow session state.

    Applies the same defensive validation the anonymous handler used: requires
    a name (truncated to 200 chars), coerces age to int and drops it if outside
    2–18, and truncates reading_ability. Returns ``None`` for entries that
    aren't usable.
    """
    if not isinstance(child, dict) or not child.get("name"):
        return None

    name = str(child["name"])[:200]

    age = child.get("age")
    if isinstance(age, str):
        try:
            age = int(age)
        except ValueError:
            age = None
    if age is not None and (age < 2 or age > 18):
        age = None

    reading_ability = str(child.get("reading_ability", ""))[:50] or None

    return {"name": name, "age": age, "reading_ability": reading_ability}

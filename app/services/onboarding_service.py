"""Shared family-onboarding logic.

Used by both the authenticated HTTP endpoint (``app.api.onboarding.onboard_family``)
and the chatflow internal handler
(``app.services.internal_api_handlers.handle_family_onboarding``) so that reader
profiles are created and linked to a parent account in exactly one place.
"""

from typing import Any, Mapping, Optional, Sequence

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status
from structlog import get_logger

from app.models import School, SchoolState
from app.models.user import User, UserAccountType
from app.repositories.school_repository import school_repository
from app.schemas.school import normalize_school_info
from app.services.experiments import get_experiments
from app.services.school_membership import bind_educator, promote_to_school_admin

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


# ── School onboarding ──────────────────────────────────────────────────
#
# name+country is NOT a safe school identity — distinct schools share a name
# within a country. So a name+country match is only treated as the same school
# when an authoritative signal agrees (an official identifier, or matching
# location); otherwise the match is ambiguous and the caller must disambiguate
# (select an existing school by id, or explicitly ask to create a new one).


class SchoolOnboardingError(Exception):
    """Base class for onboarding resolution failures (translated to HTTP by the
    route)."""


class OnboardingSchoolNotFound(SchoolOnboardingError):
    """A referenced school id does not exist."""


class OnboardingSchoolNotClaimable(SchoolOnboardingError):
    """The resolved school can't be self-claimed (already active / has an admin)."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class OnboardingSchoolAmbiguous(SchoolOnboardingError):
    """A same-name/country school exists but isn't confirmably the same one."""

    def __init__(self, candidates: list[dict]):
        self.candidates = candidates
        super().__init__("ambiguous school match")


def _norm(value: Any) -> Optional[str]:
    return value.strip().lower() if isinstance(value, str) and value.strip() else None


def _location_agrees(req_location: Optional[Mapping], school: School) -> bool:
    """True when the request's location confirms it's the same physical school
    as ``school`` — same postcode, or same suburb AND state."""
    existing = (school.info or {}).get("location") or {}
    req = req_location or {}
    rp, ep = _norm(req.get("postcode")), _norm(existing.get("postcode"))
    if rp and ep and rp == ep:
        return True
    rs, es = _norm(req.get("suburb")), _norm(existing.get("suburb"))
    rst, est = _norm(req.get("state")), _norm(existing.get("state"))
    return bool(rs and es and rs == es and rst and est and rst == est)


def _candidate_summary(school: School) -> dict:
    return {
        "school_wriveted_id": str(school.wriveted_identifier),
        "name": school.name,
        "state": school.state.value,
        "location": (school.info or {}).get("location") or {},
    }


async def _ensure_claimable(db: AsyncSession, school: School) -> None:
    if school.state == SchoolState.ACTIVE:
        raise OnboardingSchoolNotClaimable("active")
    if await school_repository.ahas_admin(db, school.id):
        raise OnboardingSchoolNotClaimable("has_admin")


async def resolve_and_claim_onboarding_school(
    db: AsyncSession,
    *,
    user: User,
    school_wriveted_id,
    school_name: Optional[str],
    country_code: Optional[str],
    official_identifier: Optional[str],
    location: Optional[Mapping],
    create_new_school: bool,
    onboarding_info: dict,
) -> School:
    """Resolve (or create) the school for a self-serve signup and bind the user
    as its administrator. Serialised so concurrent registrations converge on one
    record with one admin. Raises the domain errors above for the route to map."""
    if school_wriveted_id is not None:
        existing = (
            (
                await db.execute(
                    select(School).where(
                        School.wriveted_identifier == school_wriveted_id
                    )
                )
            )
            .scalars()
            .first()
        )
        if existing is None:
            raise OnboardingSchoolNotFound()
        school = await school_repository.alock_school_for_update(db, existing.id)
        await _ensure_claimable(db, school)
    else:
        # Advisory lock the identity key so concurrent first-registrations of a
        # previously-unseen (name, country) can't both insert.
        await school_repository.alock_school_identity_key(db, school_name, country_code)

        chosen: Optional[School] = None
        if official_identifier:
            chosen = await school_repository.afind_by_official_identifier(
                db, country_code, official_identifier
            )
        if chosen is None:
            matches = list(
                await school_repository.afind_name_country_matches(
                    db, school_name, country_code
                )
            )
            if matches:
                authoritative = [m for m in matches if _location_agrees(location, m)]
                if authoritative:
                    chosen = authoritative[0]
                elif not create_new_school:
                    raise OnboardingSchoolAmbiguous(
                        [_candidate_summary(m) for m in matches]
                    )
                # create_new_school=True → fall through and create a distinct one

        if chosen is None:
            school = await school_repository.acreate_onboarding_school(
                db,
                name=school_name,
                country_code=country_code,
                info={"location": dict(location) if location else {}},
            )
        else:
            school = await school_repository.alock_school_for_update(db, chosen.id)
            await _ensure_claimable(db, school)

    # Merge onboarding contact info; the school stays PENDING for staff review.
    merged = (
        onboarding_info if school.info is None else {**school.info, **onboarding_info}
    )
    if "experiments" not in merged:
        merged["experiments"] = get_experiments({})
    school.info = normalize_school_info(merged)
    school.state = SchoolState.PENDING

    if user.type != UserAccountType.SCHOOL_ADMIN:
        await promote_to_school_admin(db, user, school)
    else:
        await bind_educator(db, user.id, school.id)
    await db.flush()
    return school

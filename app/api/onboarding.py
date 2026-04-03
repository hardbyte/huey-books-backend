"""Self-service onboarding endpoints for schools and families."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field, StringConstraints
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from starlette import status
from structlog import get_logger
from typing_extensions import Annotated

from app.api.dependencies.async_db_dep import DBSessionDep
from app.api.dependencies.security import get_current_active_user
from app.models import School, SchoolAdmin, SchoolState
from app.models.school import SchoolBookbotType
from app.models.user import User, UserAccountType
from app.repositories.event_repository import event_repository
from app.services.experiments import get_experiments

logger = get_logger()

router = APIRouter(
    prefix="/onboarding",
    tags=["Onboarding"],
)

# Types that can be safely promoted — any other type is rejected
_PROMOTABLE_TO_SCHOOL_ADMIN = {
    UserAccountType.PUBLIC,
    UserAccountType.STUDENT,
    UserAccountType.SUPPORTER,
}
_PROMOTABLE_TO_PARENT = {
    UserAccountType.PUBLIC,
    UserAccountType.STUDENT,
    UserAccountType.SUPPORTER,
}


class SchoolLocationInput(BaseModel):
    state: Optional[str] = Field(None, max_length=100)
    postcode: Optional[str] = Field(None, max_length=20)
    suburb: Optional[str] = Field(None, max_length=200)


class SchoolOnboardingRequest(BaseModel):
    school_wriveted_id: Optional[UUID] = None

    school_name: Optional[str] = Field(None, max_length=300)
    country_code: Optional[
        Annotated[str, StringConstraints(min_length=3, max_length=3)]
    ] = None
    location: Optional[SchoolLocationInput] = None

    contact_name: str = Field(max_length=200)
    contact_email: EmailStr
    contact_role: str = Field(max_length=100)
    contact_phone: Optional[str] = Field(None, max_length=50)
    student_count_estimate: Optional[int] = Field(None, ge=1, le=100000)
    message: Optional[str] = Field(None, max_length=2000)


class SchoolOnboardingResponse(BaseModel):
    school_wriveted_id: UUID
    school_name: str
    school_state: SchoolState
    message: str


@router.post("/school", response_model=SchoolOnboardingResponse)
async def onboard_school(
    request: SchoolOnboardingRequest,
    db: DBSessionDep,
    current_user: User = Depends(get_current_active_user),
):
    """Self-service school onboarding.

    Creates or selects a school, promotes the user to SchoolAdmin,
    binds them as the school's administrator, and sets the school
    to PENDING for admin review.
    """
    # Resolve or create the school
    if request.school_wriveted_id:
        result = await db.execute(
            select(School).where(
                School.wriveted_identifier == request.school_wriveted_id
            )
        )
        school = result.scalars().first()
        if school is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="School not found",
            )
        if school.state == SchoolState.ACTIVE:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This school is already active. Contact us if you need access.",
            )
        # Prevent hijacking a school that already has an admin
        admin_result = await db.execute(
            select(SchoolAdmin).where(SchoolAdmin.school_id == school.id)
        )
        if admin_result.scalars().first() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This school already has an administrator. Contact us for access.",
            )
    else:
        if not request.school_name or not request.country_code:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="school_name and country_code are required when creating a new school",
            )
        try:
            school = School(
                name=request.school_name,
                country_code=request.country_code,
                state=SchoolState.PENDING,
                bookbot_type=SchoolBookbotType.HUEY_BOOKS,
                info={
                    "location": request.location.model_dump()
                    if request.location
                    else {},
                    "experiments": get_experiments({}),
                },
            )
            db.add(school)
            await db.flush()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A school with this name already exists in this country",
            )

    # Store onboarding contact info
    onboarding_info = {
        "onboarding": {
            "contact_name": request.contact_name,
            "contact_email": request.contact_email,
            "contact_role": request.contact_role,
            "contact_phone": request.contact_phone,
            "student_count_estimate": request.student_count_estimate,
            "message": request.message,
        }
    }
    if school.info is None:
        school.info = onboarding_info
    else:
        school.info = {**school.info, **onboarding_info}

    school.state = SchoolState.PENDING

    # Promote user to SchoolAdmin if needed
    if current_user.type != UserAccountType.SCHOOL_ADMIN:
        await _promote_to_school_admin(db, current_user, school)

    # Bind user to school via educators table
    await db.execute(
        text(
            "INSERT INTO educators (id, school_id) VALUES (:uid, :school_id) "
            "ON CONFLICT (id) DO UPDATE SET school_id = :school_id"
        ),
        {"uid": current_user.id, "school_id": school.id},
    )
    await db.flush()

    # Create an event for admin visibility
    await event_repository.acreate(
        session=db,
        title="School onboarding request",
        description=f"Onboarding request for {school.name}",
        info={
            "school_name": school.name,
            "school_wriveted_id": str(school.wriveted_identifier),
            "contact_name": request.contact_name,
            "contact_email": request.contact_email,
            "contact_role": request.contact_role,
            "student_count": request.student_count_estimate,
        },
        school=school,
        commit=False,
    )

    await db.commit()

    logger.info(
        "School onboarding completed",
        school_name=school.name,
        school_id=str(school.wriveted_identifier),
        user_email=current_user.email,
    )

    return SchoolOnboardingResponse(
        school_wriveted_id=school.wriveted_identifier,
        school_name=school.name,
        school_state=school.state,
        message="Your school is pending review. We'll be in touch shortly!",
    )


async def _promote_to_school_admin(
    db: DBSessionDep,
    user: User,
    school: School,
) -> None:
    """Promote a user to SchoolAdmin type, preserving their identity."""
    if user.type not in _PROMOTABLE_TO_SCHOOL_ADMIN:
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

    # Delete from the current type's subclass table
    safe_type_table_map = {
        UserAccountType.PUBLIC: "public_readers",
        UserAccountType.STUDENT: "students",
        UserAccountType.SUPPORTER: "supporters",
    }

    subclass_table = safe_type_table_map.get(user.type)
    if subclass_table:
        await db.execute(
            text(f"DELETE FROM {subclass_table} WHERE id = :uid"),
            {"uid": user_id},
        )

    # Also remove from readers if present (PUBLIC/STUDENT inherit from Reader)
    await db.execute(
        text("DELETE FROM readers WHERE id = :uid"),
        {"uid": user_id},
    )

    # Update the user type
    await db.execute(
        text("UPDATE users SET type = :new_type WHERE id = :uid"),
        {"new_type": UserAccountType.SCHOOL_ADMIN.value.upper(), "uid": user_id},
    )

    # Insert into educators (parent of school_admins in inheritance)
    await db.execute(
        text(
            "INSERT INTO educators (id, school_id) VALUES (:uid, :school_id) "
            "ON CONFLICT (id) DO UPDATE SET school_id = :school_id"
        ),
        {"uid": user_id, "school_id": school.id},
    )

    # Insert into school_admins
    await db.execute(
        text(
            "INSERT INTO school_admins (id) VALUES (:uid) ON CONFLICT (id) DO NOTHING"
        ),
        {"uid": user_id},
    )

    await db.flush()


# ── Family onboarding ─────────────────────────────────────────────────


class ChildInfo(BaseModel):
    name: str = Field(max_length=200)
    age: Optional[int] = Field(None, ge=2, le=18)
    reading_ability: Optional[str] = Field(None, max_length=50)
    interests: Optional[list[str]] = Field(None, max_length=20)


class FamilyOnboardingRequest(BaseModel):
    parent_name: str = Field(max_length=200)
    children: list[ChildInfo] = Field(min_length=1, max_length=10)


class FamilyOnboardingResponse(BaseModel):
    parent_id: UUID
    children_created: int
    message: str


@router.post("/family", response_model=FamilyOnboardingResponse)
async def onboard_family(
    request: FamilyOnboardingRequest,
    db: DBSessionDep,
    current_user: User = Depends(get_current_active_user),
):
    """Self-service family onboarding.

    Promotes the authenticated user to Parent type and creates
    child reader accounts linked to them.
    """
    from app.models.public_reader import PublicReader

    user_id = current_user.id

    # Promote to Parent if needed
    if current_user.type != UserAccountType.PARENT:
        if current_user.type not in _PROMOTABLE_TO_PARENT:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Your account type ({current_user.type.value}) cannot be converted to a parent account. Contact support.",
            )

        safe_type_table_map = {
            UserAccountType.PUBLIC: "public_readers",
            UserAccountType.STUDENT: "students",
            UserAccountType.SUPPORTER: "supporters",
        }
        subclass_table = safe_type_table_map.get(current_user.type)
        if subclass_table:
            await db.execute(
                text(f"DELETE FROM {subclass_table} WHERE id = :uid"),
                {"uid": user_id},
            )

        # Also remove from readers if present
        await db.execute(
            text("DELETE FROM readers WHERE id = :uid"),
            {"uid": user_id},
        )

        await db.execute(
            text("UPDATE users SET type = :new_type, name = :name WHERE id = :uid"),
            {
                "new_type": UserAccountType.PARENT.value.upper(),
                "name": request.parent_name,
                "uid": user_id,
            },
        )

        # Insert into parents table
        await db.execute(
            text("INSERT INTO parents (id) VALUES (:uid) ON CONFLICT (id) DO NOTHING"),
            {"uid": user_id},
        )

        await db.flush()

    # Create child readers
    children_created = 0
    for child in request.children:
        child_reader = PublicReader(
            name=child.name,
            first_name=child.name,
            parent_id=user_id,
            huey_attributes={
                "age": child.age,
                "reading_ability": child.reading_ability,
                "interests": child.interests,
            },
        )
        db.add(child_reader)
        children_created += 1

    # Create event
    await event_repository.acreate(
        session=db,
        title="Family onboarding",
        description=f"Family onboarding with {children_created} child(ren)",
        info={
            "parent_name": request.parent_name,
            "children": [c.model_dump() for c in request.children],
        },
        commit=False,
    )

    await db.commit()

    logger.info(
        "Family onboarding completed",
        user_id=str(user_id),
        children=children_created,
    )

    return FamilyOnboardingResponse(
        parent_id=user_id,
        children_created=children_created,
        message=f"Welcome! {children_created} reader profile(s) created.",
    )

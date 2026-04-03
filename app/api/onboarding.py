"""Self-service onboarding endpoints for schools and families."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette import status
from structlog import get_logger

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


class SchoolOnboardingRequest(BaseModel):
    # Option A: select existing school
    school_wriveted_id: Optional[UUID] = None

    # Option B: create new school
    school_name: Optional[str] = None
    country_code: Optional[str] = None
    location: Optional[dict] = None

    # Contact details
    contact_name: str
    contact_email: EmailStr
    contact_role: str
    contact_phone: Optional[str] = None
    student_count_estimate: Optional[int] = None
    message: Optional[str] = None


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
                    "location": request.location or {},
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
    else:
        # Already a SchoolAdmin — just bind to this school
        current_user.school_id = school.id
        db.add(current_user)

    # Set the admin relationship on the school
    result = await db.execute(
        select(SchoolAdmin).where(SchoolAdmin.id == current_user.id)
    )
    school_admin = result.scalars().first()
    if school_admin:
        school.admin = school_admin

    await db.flush()

    # Create an event for admin visibility
    await event_repository.acreate(
        session=db,
        title="School onboarding request",
        description=f"{request.contact_name} ({request.contact_role}) requested onboarding for {school.name}",
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
    from sqlalchemy import text

    user_id = user.id

    logger.info(
        "Promoting user to SchoolAdmin",
        user_id=str(user_id),
        old_type=user.type,
        school=school.name,
    )

    # Delete from the current type's subclass table.
    # Parent is excluded — deleting from parents would cascade-fail
    # due to foreign keys from readers. Parents wanting to become
    # SchoolAdmins need manual admin intervention.
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
    elif user.type == UserAccountType.PARENT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Parent accounts cannot be converted to school admin. Please contact support.",
        )

    # Update the user type and insert the school_admin row
    await db.execute(
        text("UPDATE users SET type = :new_type WHERE id = :uid"),
        {"new_type": UserAccountType.SCHOOL_ADMIN.value.upper(), "uid": user_id},
    )

    # Insert into educators (parent of school_admins in inheritance)
    try:
        await db.execute(
            text(
                "INSERT INTO educators (id, school_id) VALUES (:uid, :school_id) "
                "ON CONFLICT (id) DO UPDATE SET school_id = :school_id"
            ),
            {"uid": user_id, "school_id": school.id},
        )
    except IntegrityError:
        pass

    # Insert into school_admins
    try:
        await db.execute(
            text(
                "INSERT INTO school_admins (id) VALUES (:uid) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"uid": user_id},
        )
    except IntegrityError:
        pass

    await db.flush()

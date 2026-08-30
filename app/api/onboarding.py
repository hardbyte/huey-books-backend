"""Self-service onboarding endpoints for schools and families."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field, StringConstraints, model_validator
from starlette import status
from structlog import get_logger
from typing_extensions import Annotated

from app.api.dependencies.async_db_dep import DBSessionDep
from app.api.dependencies.security import get_current_active_user
from app.config import get_settings
from app.models import SchoolState
from app.models.user import User
from app.repositories.event_repository import event_repository
from app.schemas.school import normalize_school_info
from app.services.background_tasks import queue_background_task
from app.services.email_notification import (
    EmailType,
    send_email_reliable,
    trigger_email_delivery_async,
)
from app.services.experiments import get_experiments
from app.services.onboarding_service import (
    OnboardingSchoolAmbiguous,
    OnboardingSchoolNotClaimable,
    OnboardingSchoolNotFound,
    resolve_and_claim_onboarding_school,
)
from app.services.school_emails import (
    render_school_registered_html,
    render_staff_new_school_alert_html,
)

logger = get_logger()

router = APIRouter(
    prefix="/onboarding",
    tags=["Onboarding"],
)


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
    # Authoritative school identifier (e.g. government/UDISE id) when known.
    official_identifier: Optional[str] = Field(None, max_length=512)
    location: Optional[SchoolLocationInput] = None
    # Set true to deliberately create a distinct new school when an ambiguous
    # same-name/country match exists.
    create_new_school: bool = False

    contact_name: str = Field(max_length=200)
    contact_email: EmailStr
    contact_role: str = Field(max_length=100)
    contact_phone: Optional[str] = Field(None, max_length=50)
    student_count_estimate: Optional[int] = Field(None, ge=1, le=100000)
    message: Optional[str] = Field(None, max_length=2000)

    @model_validator(mode="after")
    def _require_existing_id_or_new_school_details(self):
        # Either select an existing school by id, or provide the details to
        # create a new one — not neither.
        if self.school_wriveted_id is None and not (
            self.school_name and self.country_code
        ):
            raise ValueError(
                "Provide school_wriveted_id, or both school_name and country_code."
            )
        return self


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

    Resolves (or creates) the school, binds the user as its administrator, and
    leaves it PENDING for admin review. School resolution/claiming lives in the
    service layer; this route only translates domain outcomes to HTTP.
    """
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
    try:
        school = await resolve_and_claim_onboarding_school(
            db,
            user=current_user,
            school_wriveted_id=request.school_wriveted_id,
            school_name=request.school_name,
            country_code=request.country_code,
            official_identifier=request.official_identifier,
            location=request.location.model_dump() if request.location else None,
            create_new_school=request.create_new_school,
            onboarding_info=onboarding_info,
        )
    except OnboardingSchoolNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="School not found"
        )
    except OnboardingSchoolNotClaimable as e:
        detail = (
            "This school is already active. Contact us if you need access."
            if e.reason == "active"
            else "This school already has an administrator. Contact us for access."
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
    except OnboardingSchoolAmbiguous as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": (
                    "More than one school matches this name and country. Select an "
                    "existing school (school_wriveted_id) or set create_new_school "
                    "to register a distinct new one."
                ),
                "candidates": e.candidates,
            },
        )

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

    # Commit the signup first: it is the transaction of record and must not
    # fail if the notification emails can't be queued.
    await db.commit()

    try:
        await _queue_onboarding_emails(db, school, request)
        await db.commit()
        await trigger_email_delivery_async()
    except Exception as e:
        logger.warning("Failed to queue onboarding emails", error=str(e))

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
        message="Your school is registered. Start your subscription to activate it.",
    )


async def _queue_onboarding_emails(db, school, request):
    """Queue the staff signup alert and the contact's confirmation email.

    Added to the request session (not committed here) so the endpoint's commit
    persists them alongside the school and event.
    """
    settings = get_settings()
    from_email = settings.BROADCAST_FROM_EMAIL

    if settings.STAFF_ALERT_EMAILS:
        await send_email_reliable(
            db=db,
            email_data={
                "from_email": from_email,
                "from_name": "Huey Books",
                "to_emails": settings.STAFF_ALERT_EMAILS,
                "subject": f"New school signup: {school.name}",
                "html_content": render_staff_new_school_alert_html(
                    school_name=school.name,
                    wriveted_id=str(school.wriveted_identifier),
                    contact_name=request.contact_name,
                    contact_email=request.contact_email,
                    contact_role=request.contact_role,
                    country_code=school.country_code,
                    student_count_estimate=request.student_count_estimate,
                    message=request.message,
                ),
            },
            email_type=EmailType.SYSTEM,
        )

    if request.contact_email:
        await send_email_reliable(
            db=db,
            email_data={
                "from_email": from_email,
                "from_name": "Huey Books",
                "to_emails": [request.contact_email],
                "subject": f"{school.name} — activate your Huey Books school",
                "html_content": render_school_registered_html(
                    school.name,
                    request.contact_name,
                    activate_url=f"{settings.HUEY_BOOKS_APP_URL.rstrip('/')}/school/activate",
                ),
            },
            email_type=EmailType.ONBOARDING,
        )


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
    from app.services.onboarding_service import create_linked_family_readers

    user_id = current_user.id

    children_created = await create_linked_family_readers(
        db,
        user=current_user,
        parent_name=request.parent_name,
        children=[c.model_dump() for c in request.children],
    )

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

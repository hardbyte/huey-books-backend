"""School referral invitation endpoints.

An active, paying school invites a peer; the peer's admin accepts via a tokenised
link and their school gets a free-trial grant. See
``app/services/school_invitations.py``.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from structlog import get_logger

from app.api.dependencies.async_db_dep import DBSessionDep
from app.api.dependencies.school import aget_school_from_wriveted_id
from app.api.dependencies.security import (
    get_current_active_superuser,
    get_current_active_user,
)
from app.config import get_settings
from app.models import School
from app.models.school_invitation import SchoolInvitation, SchoolInvitationStatus
from app.models.user import User
from app.permissions import Permission
from app.schemas.school_invitation import (
    GrantBonusInvites,
    SchoolInvitationAcceptResponse,
    SchoolInvitationAllowance,
    SchoolInvitationCreate,
    SchoolInvitationDetail,
    SchoolInvitationPreview,
)
from app.services import school_invitations as invites
from app.services.email_notification import EmailType, send_email_reliable
from app.services.school_emails import (
    render_school_activated_html,
    render_school_invite_html,
)

logger = get_logger()

router = APIRouter(tags=["School Invitations"])


def _accept_url(token: str) -> str:
    base = get_settings().HUEY_BOOKS_APP_URL.rstrip("/")
    return f"{base}/school/invited?token={token}"


async def _queue_invite_email(
    db: DBSessionDep,
    *,
    inviter_display_name: str,
    subject: str,
    invitation: SchoolInvitation,
) -> None:
    """Best-effort: queue the invitation email. A failure here must not undo the
    already-committed invitation (safe to read invitation.* post-commit —
    expire_on_commit is False)."""
    settings = get_settings()
    try:
        await send_email_reliable(
            db=db,
            email_data={
                "from_email": settings.BROADCAST_FROM_EMAIL,
                "from_name": "Huey Books",
                "to_emails": [invitation.invited_contact_email],
                "subject": subject,
                "html_content": render_school_invite_html(
                    inviter_school_name=inviter_display_name,
                    invited_school_name=invitation.invited_school_name,
                    accept_url=_accept_url(invitation.token),
                    grant_days=invitation.grant_days,
                    message=invitation.message,
                ),
            },
            email_type=EmailType.ONBOARDING,
        )
        await db.commit()
    except Exception as e:
        logger.warning("Failed to queue invitation email", error=str(e))


@router.post(
    "/school/{wriveted_identifier}/invitations",
    response_model=SchoolInvitationDetail,
    status_code=status.HTTP_201_CREATED,
)
async def send_invitation(
    payload: SchoolInvitationCreate,
    db: DBSessionDep,
    school: School = Permission("update", aget_school_from_wriveted_id),
    user: User = Depends(get_current_active_user),
):
    """Invite another school to a free trial (paying inviters only)."""
    invitation = await invites.create_invitation(db, school, user, payload)
    detail = SchoolInvitationDetail.model_validate(invitation)
    school_name = school.name
    await db.commit()

    await _queue_invite_email(
        db,
        inviter_display_name=school_name,
        subject=f"{school_name} invited your school to Huey Books",
        invitation=invitation,
    )
    return detail


@router.get(
    "/school/{wriveted_identifier}/invitations",
    response_model=list[SchoolInvitationDetail],
)
async def list_school_invitations(
    db: DBSessionDep,
    school: School = Permission("update", aget_school_from_wriveted_id),
):
    """List invitations this school has sent, with current status."""
    rows = await invites.list_invitations(db, school.wriveted_identifier)
    details = [SchoolInvitationDetail.model_validate(r) for r in rows]
    await db.commit()  # persist any lazy SENT→EXPIRED transitions
    return details


@router.get(
    "/school/{wriveted_identifier}/invitations/allowance",
    response_model=SchoolInvitationAllowance,
)
async def get_invitation_allowance(
    db: DBSessionDep,
    school: School = Permission("update", aget_school_from_wriveted_id),
):
    """How many invitations this school can still send (base + earned + staff bonus)."""
    allowance = await invites.invite_allowance(db, school)
    return SchoolInvitationAllowance(**allowance)


@router.post(
    "/admin/schools/{wriveted_identifier}/invitations/grant-bonus",
    response_model=SchoolInvitationAllowance,
)
async def grant_bonus_invitations(
    payload: GrantBonusInvites,
    wriveted_identifier: UUID,
    db: DBSessionDep,
    account=Depends(get_current_active_superuser),
):
    """Staff action: grant a school extra invitations."""
    school = (
        await db.execute(
            select(School).where(School.wriveted_identifier == wriveted_identifier)
        )
    ).scalar_one_or_none()
    if school is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "School not found.")
    await invites.grant_bonus_invites(db, school, payload.additional)
    allowance = await invites.invite_allowance(db, school)
    await db.commit()
    return SchoolInvitationAllowance(**allowance)


@router.post(
    "/admin/invitations",
    response_model=SchoolInvitationDetail,
    status_code=status.HTTP_201_CREATED,
)
async def send_staff_invitation(
    payload: SchoolInvitationCreate,
    db: DBSessionDep,
    account=Depends(get_current_active_superuser),
):
    """Staff can invite any school directly (no source school, gate, or limit)."""
    user = account if isinstance(account, User) else None
    invitation = await invites.create_staff_invitation(db, user, payload)
    detail = SchoolInvitationDetail.model_validate(invitation)
    await db.commit()

    await _queue_invite_email(
        db,
        inviter_display_name="The Huey Books team",
        subject="You're invited to Huey Books",
        invitation=invitation,
    )
    return detail


@router.post(
    "/school/{wriveted_identifier}/invitations/{invitation_id}/revoke",
    response_model=SchoolInvitationDetail,
)
async def revoke_school_invitation(
    invitation_id: UUID,
    db: DBSessionDep,
    school: School = Permission("update", aget_school_from_wriveted_id),
):
    """Withdraw a sent invitation."""
    invitation = (
        (
            await db.execute(
                select(SchoolInvitation).where(SchoolInvitation.id == invitation_id)
            )
        )
        .scalars()
        .first()
    )
    if invitation is None or invitation.inviter_school_id != school.wriveted_identifier:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation not found.")
    await invites.revoke_invitation(db, invitation)
    detail = SchoolInvitationDetail.model_validate(invitation)
    await db.commit()
    return detail


@router.get("/invitations/{token}", response_model=SchoolInvitationPreview)
async def preview_invitation(token: str, db: DBSessionDep, response: Response):
    """Public (token-addressed) preview for the accept page."""
    # The token is a bearer credential in the URL — don't let it be cached.
    response.headers["Cache-Control"] = "no-store"
    invitation = await invites.get_invitation_by_token(db, token)
    if invitation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation not found.")
    inviter_name = (
        await db.execute(
            select(School.name).where(
                School.wriveted_identifier == invitation.inviter_school_id
            )
        )
    ).scalar() or "A school"
    return SchoolInvitationPreview(
        inviter_school_name=inviter_name,
        invited_school_name=invitation.invited_school_name,
        grant_days=invitation.grant_days,
        status=invitation.status,
        expired=invites._is_expired(invitation)
        and invitation.status == SchoolInvitationStatus.SENT,
    )


@router.post(
    "/invitations/{token}/accept", response_model=SchoolInvitationAcceptResponse
)
async def accept_invitation(
    token: str,
    db: DBSessionDep,
    user: User = Depends(get_current_active_user),
):
    """Accept an invitation: the invited school goes live with a free-trial grant."""
    school, expiration = await invites.accept_invitation(db, token, user)
    school_id = school.wriveted_identifier
    school_name = school.name
    await db.commit()

    settings = get_settings()
    admin_url = (
        getattr(settings, "SCHOOL_ADMIN_URL", None) or "https://admin.hueybooks.com"
    )
    if not user.email:
        return SchoolInvitationAcceptResponse(
            school_wriveted_id=school_id,
            school_name=school_name,
            access_until=expiration,
            message=f"Welcome! {school_name} is live on Huey Books.",
        )
    try:
        await send_email_reliable(
            db=db,
            email_data={
                "from_email": settings.BROADCAST_FROM_EMAIL,
                "from_name": "Huey Books",
                "to_emails": [user.email],
                "subject": f"{school_name} is live on Huey Books",
                "html_content": render_school_activated_html(
                    school_name, user.name, admin_url
                ),
            },
            email_type=EmailType.ONBOARDING,
        )
        await db.commit()
    except Exception as e:
        logger.warning("Failed to queue invite activation email", error=str(e))

    return SchoolInvitationAcceptResponse(
        school_wriveted_id=school_id,
        school_name=school_name,
        access_until=expiration,
        message=f"Welcome! {school_name} is live on Huey Books.",
    )

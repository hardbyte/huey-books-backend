"""School referral invitations: a paying school invites a peer, who gets a free
trial (a comped grant) when their admin accepts. See
``docs/school-invitations-design.md``."""

import secrets
from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status
from structlog import get_logger

from app.config import get_settings
from app.models import Country, School, SchoolAdmin
from app.models.school import SchoolBookbotType, SchoolState
from app.models.school_invitation import SchoolInvitation, SchoolInvitationStatus
from app.models.subscription import Subscription
from app.models.user import User, UserAccountType
from app.schemas.school_invitation import SchoolInvitationCreate
from app.services.school_access import grant_invite_access, invite_grant_id
from app.services.school_membership import promote_to_school_admin

logger = get_logger()


async def _has_paying_subscription(session: AsyncSession, school: School) -> bool:
    """True if the school has an active *paying* subscription (real Stripe
    customer — comped grants have an empty ``stripe_customer_id``)."""
    q = select(
        exists().where(
            Subscription.school_id == school.wriveted_identifier,
            Subscription.is_active.is_(True),
            Subscription.stripe_customer_id != "",
        )
    )
    return bool((await session.execute(q)).scalar())


async def _school_has_admin(session: AsyncSession, school: School) -> bool:
    q = select(exists().where(SchoolAdmin.school_id == school.id))
    return bool((await session.execute(q)).scalar())


async def _active_invite_count(session: AsyncSession, inviter_school_id: UUID) -> int:
    """Invites that occupy a slot: accepted ones, plus sent ones not yet expired.
    A lapsed (past ``expires_at``) SENT invite frees its slot even before the lazy
    SENT→EXPIRED transition is persisted."""
    now = datetime.utcnow()
    q = select(func.count(SchoolInvitation.id)).where(
        SchoolInvitation.inviter_school_id == inviter_school_id,
        or_(
            SchoolInvitation.status == SchoolInvitationStatus.ACCEPTED,
            and_(
                SchoolInvitation.status == SchoolInvitationStatus.SENT,
                SchoolInvitation.expires_at > now,
            ),
        ),
    )
    return int((await session.execute(q)).scalar() or 0)


async def _email_is_existing_school_admin(session: AsyncSession, email: str) -> bool:
    q = select(
        exists().where(
            func.lower(User.email) == email.lower(),
            User.type == UserAccountType.SCHOOL_ADMIN,
        )
    )
    return bool((await session.execute(q)).scalar())


async def create_invitation(
    session: AsyncSession,
    inviter_school: School,
    inviter_user: User,
    payload: SchoolInvitationCreate,
) -> SchoolInvitation:
    """Validate and persist a referral invitation, ready to email."""
    settings = get_settings()

    # Lock the inviter school row so concurrent sends can't both pass the cap.
    await session.execute(
        select(School.id).where(School.id == inviter_school.id).with_for_update()
    )

    if inviter_school.state != SchoolState.ACTIVE:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your school is not active.")
    if settings.INVITE_REQUIRE_PAYING_INVITER and not await _has_paying_subscription(
        session, inviter_school
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Only schools with an active paid subscription can invite other schools.",
        )

    if await _active_invite_count(session, inviter_school.wriveted_identifier) >= (
        settings.INVITE_MAX_PER_SCHOOL
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"You've reached the limit of {settings.INVITE_MAX_PER_SCHOOL} invitations.",
        )

    invited_school: Optional[School] = None
    if payload.invited_school_wriveted_id is not None:
        invited_school = (
            (
                await session.execute(
                    select(School).where(
                        School.wriveted_identifier == payload.invited_school_wriveted_id
                    )
                )
            )
            .scalars()
            .first()
        )
        if invited_school is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Invited school not found.")
        if invited_school.wriveted_identifier == inviter_school.wriveted_identifier:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "You can't invite your own school."
            )
        if invited_school.state == SchoolState.ACTIVE:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "That school is already on Huey Books."
            )
        # Already invited & accepted → don't re-invite.
        already = (
            await session.execute(
                select(
                    exists().where(
                        SchoolInvitation.invited_school_id
                        == invited_school.wriveted_identifier,
                        SchoolInvitation.status == SchoolInvitationStatus.ACCEPTED,
                    )
                )
            )
        ).scalar()
        if already:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "That school has already accepted an invitation.",
            )

    # Free-text new-school path: validate the country FK now so send doesn't 500
    # on a bad code at flush time.
    if invited_school is None and payload.country_code is not None:
        known_country = (
            await session.execute(
                select(exists().where(Country.id == payload.country_code))
            )
        ).scalar()
        if not known_country:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Unknown country code '{payload.country_code}'.",
            )

    if await _email_is_existing_school_admin(session, payload.contact_email):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "That contact already administers a school on Huey Books.",
        )

    grant_days = payload.grant_days or settings.INVITE_GRANT_DAYS
    now = datetime.utcnow()
    invitation = SchoolInvitation(
        token=secrets.token_urlsafe(32),
        inviter_school_id=inviter_school.wriveted_identifier,
        inviter_user_id=inviter_user.id,
        invited_school_id=(
            invited_school.wriveted_identifier if invited_school else None
        ),
        invited_school_name=(
            invited_school.name if invited_school else payload.invited_school_name
        ),
        country_code=(
            invited_school.country_code if invited_school else payload.country_code
        ),
        invited_contact_email=payload.contact_email,
        invited_contact_name=payload.contact_name,
        grant_days=grant_days,
        status=SchoolInvitationStatus.SENT,
        expires_at=now + timedelta(days=settings.INVITE_EXPIRY_DAYS),
    )
    session.add(invitation)
    await session.flush()
    return invitation


async def get_invitation_by_token(
    session: AsyncSession, token: str, *, for_update: bool = False
) -> Optional[SchoolInvitation]:
    q = select(SchoolInvitation).where(SchoolInvitation.token == token)
    if for_update:
        q = q.with_for_update()
    return (await session.execute(q)).scalars().first()


def _is_expired(invitation: SchoolInvitation) -> bool:
    return datetime.utcnow() > invitation.expires_at


async def accept_invitation(
    session: AsyncSession, token: str, user: User
) -> tuple[School, Optional[datetime]]:
    """Accept an invite: activate the invited school, grant free access, bind the
    user as its admin. Returns (school, access_until)."""
    # Lock the accepting user so two concurrent accepts (two tokens, one user)
    # can't both pass the "already administers a school" check below.
    await session.execute(select(User.id).where(User.id == user.id).with_for_update())

    invitation = await get_invitation_by_token(session, token, for_update=True)
    if invitation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation not found.")

    if invitation.status == SchoolInvitationStatus.REVOKED:
        raise HTTPException(status.HTTP_409_CONFLICT, "This invitation was withdrawn.")
    if invitation.status == SchoolInvitationStatus.ACCEPTED:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This invitation has already been accepted."
        )
    if invitation.status == SchoolInvitationStatus.EXPIRED or _is_expired(invitation):
        invitation.status = SchoolInvitationStatus.EXPIRED
        raise HTTPException(status.HTTP_410_GONE, "This invitation has expired.")

    # A user who already runs another school can't be silently moved.
    if user.type == UserAccountType.SCHOOL_ADMIN:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "You already administer a school. Contact us to link another.",
        )

    # Resolve or create the invited school, locking it against concurrent flows.
    if invitation.invited_school_id is not None:
        school = (
            (
                await session.execute(
                    select(School)
                    .where(School.wriveted_identifier == invitation.invited_school_id)
                    .with_for_update()
                )
            )
            .scalars()
            .first()
        )
        if school is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Invited school not found.")
    else:
        try:
            school = School(
                name=invitation.invited_school_name,
                country_code=invitation.country_code,
                state=SchoolState.INACTIVE,
                bookbot_type=SchoolBookbotType.HUEY_BOOKS,
                info={"source": "invite"},
            )
            session.add(school)
            await session.flush()
        except IntegrityError:
            await session.rollback()
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "A school with this name already exists in this country.",
            )
        invitation.invited_school_id = school.wriveted_identifier

    # Re-check post-lock: not already active, not already administered.
    if school.state == SchoolState.ACTIVE:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "That school is already on Huey Books."
        )
    if await _school_has_admin(session, school):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This school already has an administrator."
        )

    outcome, expiration = await grant_invite_access(
        session, school, invitation.grant_days
    )
    if outcome == "already_expired":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This school has already used its free trial.",
        )

    await promote_to_school_admin(session, user, school)

    invitation.status = SchoolInvitationStatus.ACCEPTED
    invitation.accepted_at = datetime.utcnow()
    invitation.redeemed_subscription_id = invite_grant_id(school.wriveted_identifier)
    await session.flush()

    logger.info(
        "School invitation accepted",
        invitation_id=str(invitation.id),
        school=school.name,
        grant_outcome=outcome,
        access_until=str(expiration),
    )
    return school, expiration


async def revoke_invitation(
    session: AsyncSession, invitation: SchoolInvitation
) -> SchoolInvitation:
    if invitation.status != SchoolInvitationStatus.SENT:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Only sent invitations can be revoked (this one is {invitation.status.value}).",
        )
    invitation.status = SchoolInvitationStatus.REVOKED
    await session.flush()
    return invitation


async def list_invitations(
    session: AsyncSession, inviter_school_id: UUID
) -> list[SchoolInvitation]:
    rows = (
        (
            await session.execute(
                select(SchoolInvitation)
                .where(SchoolInvitation.inviter_school_id == inviter_school_id)
                .order_by(SchoolInvitation.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    # Lazily reflect expiry so listings show the true state.
    for inv in rows:
        if inv.status == SchoolInvitationStatus.SENT and _is_expired(inv):
            inv.status = SchoolInvitationStatus.EXPIRED
    return list(rows)

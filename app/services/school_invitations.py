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
from app.models.collection import Collection
from app.models.collection_item import CollectionItem
from app.models.event import Event
from app.models.school import SchoolBookbotType, SchoolState
from app.models.school_invitation import SchoolInvitation, SchoolInvitationStatus
from app.models.subscription import Subscription
from app.models.user import User, UserAccountType
from app.schemas.school_invitation import SchoolInvitationCreate
from app.services.school_access import grant_invite_access, invite_grant_id
from app.services.school_membership import promote_to_school_admin

logger = get_logger()

BOOK_REVIEWED_EVENT_TITLE = "Huey: Book reviewed"
INVITE_BONUS_INFO_KEY = "invite_bonus"


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


async def _invites_used_in_window(
    session: AsyncSession, inviter_school_id: UUID
) -> int:
    """Invites this school has spent within the current allowance window: accepted
    ones plus sent-and-not-yet-expired ones, created since the window opened. The
    allowance resets as older invitations age out of the window."""
    settings = get_settings()
    now = datetime.utcnow()
    window_start = now - timedelta(days=settings.INVITE_ALLOWANCE_WINDOW_DAYS)
    q = select(func.count(SchoolInvitation.id)).where(
        SchoolInvitation.inviter_school_id == inviter_school_id,
        SchoolInvitation.created_at > window_start,
        or_(
            SchoolInvitation.status == SchoolInvitationStatus.ACCEPTED,
            and_(
                SchoolInvitation.status == SchoolInvitationStatus.SENT,
                SchoolInvitation.expires_at > now,
            ),
        ),
    )
    return int((await session.execute(q)).scalar() or 0)


async def _earned_invite_bonus(session: AsyncSession, school: School) -> int:
    """Bonus invites a school has earned by contributing to the platform: one per
    ``INVITE_EARN_REVIEWS_PER_BONUS`` book reviews and one per
    ``INVITE_EARN_BOOKS_ADDED_PER_BONUS`` books added to its collection, capped at
    ``INVITE_EARN_MAX_BONUS``. Counted over the same rolling window as the spend
    side, so the earned allowance resets alongside it (and the review count stays
    bounded by ``Event.timestamp`` rather than scanning all history)."""
    settings = get_settings()
    window_start = datetime.utcnow() - timedelta(
        days=settings.INVITE_ALLOWANCE_WINDOW_DAYS
    )

    # Reader book reviews are Events (keyed by the integer School.id).
    reviews = int(
        (
            await session.execute(
                select(func.count(Event.id)).where(
                    Event.title == BOOK_REVIEWED_EVENT_TITLE,
                    Event.school_id == school.id,
                    Event.timestamp > window_start,
                )
            )
        ).scalar()
        or 0
    )
    # Books added = items in the school's collection (keyed by wriveted_identifier).
    books_added = int(
        (
            await session.execute(
                select(func.count(CollectionItem.id))
                .select_from(CollectionItem)
                .join(Collection, Collection.id == CollectionItem.collection_id)
                .where(Collection.school_id == school.wriveted_identifier)
            )
        ).scalar()
        or 0
    )

    earned = 0
    if settings.INVITE_EARN_REVIEWS_PER_BONUS > 0:
        earned += reviews // settings.INVITE_EARN_REVIEWS_PER_BONUS
    if settings.INVITE_EARN_BOOKS_ADDED_PER_BONUS > 0:
        earned += books_added // settings.INVITE_EARN_BOOKS_ADDED_PER_BONUS
    return min(earned, settings.INVITE_EARN_MAX_BONUS)


def _staff_granted_bonus(school: School) -> int:
    """Extra invites a staff member granted this school (stored in School.info)."""
    if not school.info:
        return 0
    try:
        return max(0, int(school.info.get(INVITE_BONUS_INFO_KEY, 0)))
    except (TypeError, ValueError):
        return 0


async def invite_allowance(session: AsyncSession, school: School) -> dict:
    """The school's full invite allowance breakdown for a given window."""
    settings = get_settings()
    base = settings.INVITE_MAX_PER_SCHOOL
    staff_bonus = _staff_granted_bonus(school)
    earned = await _earned_invite_bonus(session, school)
    used = await _invites_used_in_window(session, school.wriveted_identifier)
    total = base + staff_bonus + earned
    return {
        "base": base,
        "staff_bonus": staff_bonus,
        "earned_bonus": earned,
        "total": total,
        "used": used,
        "remaining": max(0, total - used),
        "window_days": settings.INVITE_ALLOWANCE_WINDOW_DAYS,
    }


async def grant_bonus_invites(
    session: AsyncSession, school: School, additional: int
) -> int:
    """Staff action: add ``additional`` bonus invites to a school. Returns the new
    staff-granted bonus total."""
    info = dict(school.info or {})
    current = _staff_granted_bonus(school)
    new_total = max(0, current + additional)
    info[INVITE_BONUS_INFO_KEY] = new_total
    school.info = info
    session.add(school)
    await session.flush()
    return new_total


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

    allowance = await invite_allowance(session, inviter_school)
    if allowance["remaining"] <= 0:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"You've used all {allowance['total']} of your invitations for now. "
            "Contributing more to Huey Books (or asking us) can unlock more.",
        )

    invited_school = await _resolve_and_validate_target(
        session, payload, inviter_school=inviter_school
    )
    return await _build_invitation(
        session, payload, invited_school, inviter_user, inviter_school=inviter_school
    )


async def create_staff_invitation(
    session: AsyncSession,
    staff_user: Optional[User],
    payload: SchoolInvitationCreate,
) -> SchoolInvitation:
    """Staff (Wriveted) can invite any school directly, with no source school,
    paying gate, or allowance limit."""
    invited_school = await _resolve_and_validate_target(
        session, payload, inviter_school=None
    )
    return await _build_invitation(
        session, payload, invited_school, staff_user, inviter_school=None
    )


async def _resolve_and_validate_target(
    session: AsyncSession,
    payload: SchoolInvitationCreate,
    *,
    inviter_school: Optional[School],
) -> Optional[School]:
    """Resolve/validate the invitee (existing inactive school, or a new
    name+country), shared by the school and staff invite paths."""
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
        if (
            inviter_school is not None
            and invited_school.wriveted_identifier == inviter_school.wriveted_identifier
        ):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "You can't invite your own school."
            )
        if invited_school.state == SchoolState.ACTIVE:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "That school is already on Huey Books."
            )
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

    # Don't spam a contact who already has a live (sent, unexpired) invitation.
    outstanding = (
        await session.execute(
            select(
                exists().where(
                    func.lower(SchoolInvitation.invited_contact_email)
                    == payload.contact_email.lower(),
                    SchoolInvitation.status == SchoolInvitationStatus.SENT,
                    SchoolInvitation.expires_at > datetime.utcnow(),
                )
            )
        )
    ).scalar()
    if outstanding:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "That contact already has a pending invitation.",
        )
    return invited_school


async def _build_invitation(
    session: AsyncSession,
    payload: SchoolInvitationCreate,
    invited_school: Optional[School],
    inviter_user: Optional[User],
    *,
    inviter_school: Optional[School],
) -> SchoolInvitation:
    settings = get_settings()
    grant_days = payload.grant_days or settings.INVITE_GRANT_DAYS
    now = datetime.utcnow()
    invitation = SchoolInvitation(
        token=secrets.token_urlsafe(32),
        inviter_school_id=(
            inviter_school.wriveted_identifier if inviter_school else None
        ),
        inviter_user_id=inviter_user.id if inviter_user else None,
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
        message=payload.message,
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
    # Lock and re-read the accepting user in *this* session so two concurrent
    # accepts (two tokens, one user) can't both pass the "already administers a
    # school" check: the second waits on the row lock, then sees the committed
    # type change. (The user from the auth dependency may belong to another
    # session, so we can't refresh it directly — re-query it here.)
    user = (
        await session.execute(select(User).where(User.id == user.id).with_for_update())
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")

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
        # The lazy SENT→EXPIRED transition is persisted by list_invitations; this
        # handler raises without committing, so don't pretend to write it here.
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

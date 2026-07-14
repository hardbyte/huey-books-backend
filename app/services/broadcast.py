"""Broadcast — staff announcements to a user segment via the email outbox.

Resolves a recipient audience (by account type, school, and/or country),
renders a plain-text message into safe HTML with an unsubscribe footer, and
enqueues one email per recipient through the existing SendGrid outbox.
Recipients who have opted out are always excluded. A test send delivers the
same message only to the requesting staff member.
"""

from html import escape
from typing import Optional

from jose import jwt
from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from structlog import get_logger

from app import crud
from app.config import get_settings
from app.models.educator import Educator
from app.models.school import School
from app.models.student import Student
from app.models.user import User
from app.schemas.broadcast import BroadcastAudience
from app.schemas.sendgrid import SendGridEmailData
from app.services.background_tasks import queue_background_task
from app.services.email_notification import EmailType, create_email_notification_service

logger = get_logger()

settings = get_settings()

_ALGORITHM = "HS256"
_UNSUBSCRIBE_PURPOSE = "email_unsubscribe"

FROM_NAME = "Huey Books"


def make_unsubscribe_token(user_id) -> str:
    """A long-lived signed token identifying a user for one-click unsubscribe."""
    return jwt.encode(
        {"sub": str(user_id), "purpose": _UNSUBSCRIBE_PURPOSE},
        settings.SECRET_KEY,
        algorithm=_ALGORITHM,
    )


def verify_unsubscribe_token(token: str) -> Optional[str]:
    """Return the user id encoded in an unsubscribe token, or None if invalid."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[_ALGORITHM])
    except Exception:
        return None
    if payload.get("purpose") != _UNSUBSCRIBE_PURPOSE:
        return None
    return payload.get("sub")


def unsubscribe_url(user_id) -> str:
    base = settings.WRIVETED_API_BASE_URL.rstrip("/")
    return f"{base}/v1/email/unsubscribe?token={make_unsubscribe_token(user_id)}"


def _unsubscribe_headers(user_id) -> dict[str, str]:
    """RFC 8058 one-click unsubscribe headers for better deliverability.

    The List-Unsubscribe URL accepts a POST (One-Click), so mail clients can
    unsubscribe in place; GET link-scanners can't trigger it.
    """
    return {
        "List-Unsubscribe": (
            f"<{unsubscribe_url(user_id)}>, "
            f"<mailto:{settings.BROADCAST_REPLY_TO}?subject=Unsubscribe>"
        ),
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }


def _school_member_condition(internal_school_id_subquery):
    """A condition matching users affiliated with the given school ids.

    Educators and school admins share the ``educators`` table (school_admins is
    a subclass), so ``Educator.school_id`` covers both; students are separate.
    """
    return or_(
        User.id.in_(
            select(Educator.id).where(
                Educator.school_id.in_(internal_school_id_subquery)
            )
        ),
        User.id.in_(
            select(Student.id).where(Student.school_id.in_(internal_school_id_subquery))
        ),
    )


def _resolve_recipients_query(db: Session, audience: BroadcastAudience):
    query = select(User).where(
        User.is_active.is_(True),
        User.email.isnot(None),
        User.email != "",
        User.marketing_opt_out.is_(False),
    )

    if audience.user_types:
        query = query.where(User.type.in_(audience.user_types))

    if audience.country_code:
        country_school_ids = select(School.id).where(
            School.country_code == audience.country_code
        )
        query = query.where(_school_member_condition(country_school_ids))

    if audience.school_id is not None:
        internal_school_id = db.scalar(
            select(School.id).where(School.wriveted_identifier == audience.school_id)
        )
        if internal_school_id is None:
            query = query.where(False)
        else:
            query = query.where(
                _school_member_condition(
                    select(School.id).where(School.id == internal_school_id)
                )
            )

    return query


def resolve_recipients(db: Session, audience: BroadcastAudience) -> list[User]:
    return list(db.scalars(_resolve_recipients_query(db, audience)).all())


def render_email_html(body: str, unsubscribe_link: str) -> str:
    """Render a staff-authored plain-text message to safe HTML.

    The body is escaped (defence in depth even though authors are staff); blank
    lines become paragraphs and single newlines become line breaks. A footer
    identifies the sender and provides the required unsubscribe link.
    """
    paragraphs = [block.strip() for block in body.split("\n\n") if block.strip()]
    rendered = "".join(
        f"<p>{escape(p).replace(chr(10), '<br>')}</p>" for p in paragraphs
    )
    return (
        '<div style="font-family: Arial, sans-serif; font-size: 15px; '
        'line-height: 1.5; color: #1f1f1f; max-width: 600px;">'
        f"{rendered}"
        '<hr style="border:none;border-top:1px solid #eee;margin:24px 0 12px;">'
        '<p style="font-size:12px;color:#888;">'
        "You're receiving this because you have a Huey Books account. "
        f'<a href="{unsubscribe_link}">Unsubscribe from these updates</a>.'
        "<br>Huey Books"
        "</p>"
        "</div>"
    )


def _queue_email(
    db: Session, *, to_email: str, subject: str, html: str, user_id, headers=None
):
    create_email_notification_service().send_email_via_outbox_sync(
        db,
        SendGridEmailData(
            from_email=settings.BROADCAST_FROM_EMAIL,
            from_name=FROM_NAME,
            reply_to=settings.BROADCAST_REPLY_TO,
            to_emails=[to_email],
            subject=subject,
            html_content=html,
            headers=headers,
        ),
        email_type=EmailType.MARKETING,
        user_id=str(user_id) if user_id else None,
    )


def _trigger_outbox_processing() -> None:
    """Ask the internal API to deliver queued outbox events now.

    Best-effort: the periodic outbox sweep will still deliver anything queued
    if this nudge fails, so a Cloud Tasks hiccup must not fail the request.
    """
    try:
        queue_background_task("process-outbox-events")
    except Exception as e:
        logger.warning(
            "Failed to trigger outbox processing; the scheduled sweep will deliver",
            error=str(e),
        )


def send_broadcast(
    db: Session,
    *,
    subject: str,
    body: str,
    audience: BroadcastAudience,
    account=None,
) -> int:
    """Enqueue the broadcast to all matching recipients. Returns the count."""
    recipients = resolve_recipients(db, audience)

    for user in recipients:
        html = render_email_html(body, unsubscribe_url(user.id))
        _queue_email(
            db,
            to_email=user.email,
            subject=subject,
            html=html,
            user_id=user.id,
            headers=_unsubscribe_headers(user.id),
        )

    logger.info(
        "Broadcast queued",
        recipients=len(recipients),
        user_types=[t.value for t in audience.user_types],
        country_code=audience.country_code,
        school_id=str(audience.school_id) if audience.school_id else None,
        subject=subject,
    )

    if recipients:
        crud.event.create(
            db,
            title="Broadcast sent",
            description=f"'{subject}' queued to {len(recipients)} users",
            info={
                "subject": subject,
                "recipients": len(recipients),
                "user_types": [t.value for t in audience.user_types],
                "country_code": audience.country_code,
            },
            account=account,
        )
        # The request session never commits on its own; without this the
        # queued outbox rows are rolled back when the session closes.
        db.commit()
        _trigger_outbox_processing()

    return len(recipients)


def send_test(db: Session, *, subject: str, body: str, account) -> int:
    """Send the composed message only to the requesting staff member."""
    if not getattr(account, "email", None):
        return 0
    html = render_email_html(body, unsubscribe_url(account.id))
    _queue_email(
        db,
        to_email=account.email,
        subject=f"[Test] {subject}",
        html=html,
        user_id=account.id,
    )
    # The request session never commits on its own; without this the
    # queued outbox row is rolled back when the session closes.
    db.commit()
    _trigger_outbox_processing()
    logger.info("Broadcast test queued", to=account.email, subject=subject)
    return 1


def unsubscribe_user(db: Session, token: str) -> bool:
    """Mark the token's user as opted out. Returns True on success."""
    user_id = verify_unsubscribe_token(token)
    if not user_id:
        return False
    user = db.get(User, user_id)
    if user is None:
        return False
    user.marketing_opt_out = True
    db.add(user)
    db.commit()
    return True

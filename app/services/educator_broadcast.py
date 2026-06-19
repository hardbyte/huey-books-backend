"""Educator broadcast — staff announcements to educators via the email outbox.

Resolves a recipient audience (all educators, or one school's educators),
renders a plain-text message into safe HTML with an unsubscribe footer, and
enqueues one MARKETING email per recipient through the existing SendGrid
outbox. Recipients who have opted out are always excluded.
"""

from html import escape
from typing import Optional
from uuid import UUID

from jose import jwt
from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from structlog import get_logger

from app import crud
from app.config import get_settings
from app.models.educator import Educator
from app.models.school import School
from app.models.school_admin import SchoolAdmin
from app.models.user import User, UserAccountType
from app.schemas.sendgrid import SendGridEmailData
from app.services.email_notification import EmailType, create_email_notification_service

logger = get_logger()

settings = get_settings()

_ALGORITHM = "HS256"
_UNSUBSCRIBE_PURPOSE = "email_unsubscribe"

FROM_EMAIL = "hello@hueybooks.com"
FROM_NAME = "Huey Books"

EDUCATOR_TYPES = [UserAccountType.EDUCATOR, UserAccountType.SCHOOL_ADMIN]


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


def _resolve_recipients_query(db: Session, scope: str, school_id: Optional[UUID]):
    query = select(User).where(
        User.type.in_(EDUCATOR_TYPES),
        User.is_active.is_(True),
        User.email.isnot(None),
        User.email != "",
        User.marketing_opt_out.is_(False),
    )

    if scope == "school":
        if school_id is None:
            raise ValueError("school_id is required when scope is 'school'")
        internal_school_id = db.scalar(
            select(School.id).where(School.wriveted_identifier == school_id)
        )
        if internal_school_id is None:
            # Unknown school -> no recipients (caller surfaces the empty count).
            query = query.where(False)
        else:
            query = query.where(
                or_(
                    User.id.in_(
                        select(Educator.id).where(
                            Educator.school_id == internal_school_id
                        )
                    ),
                    User.id.in_(
                        select(SchoolAdmin.id).where(
                            SchoolAdmin.school_id == internal_school_id
                        )
                    ),
                )
            )

    return query


def resolve_recipients(
    db: Session, scope: str, school_id: Optional[UUID]
) -> list[User]:
    return list(db.scalars(_resolve_recipients_query(db, scope, school_id)).all())


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
        "You're receiving this because you use Huey Books with your school. "
        f'<a href="{unsubscribe_link}">Unsubscribe from these updates</a>.'
        "<br>Huey Books"
        "</p>"
        "</div>"
    )


def send_broadcast(
    db: Session,
    *,
    subject: str,
    body: str,
    scope: str = "all_educators",
    school_id: Optional[UUID] = None,
    account=None,
) -> int:
    """Enqueue the broadcast to all matching recipients. Returns the count."""
    recipients = resolve_recipients(db, scope, school_id)
    email_service = create_email_notification_service()

    for user in recipients:
        html = render_email_html(body, unsubscribe_url(user.id))
        email_service.send_email_via_outbox_sync(
            db,
            SendGridEmailData(
                from_email=FROM_EMAIL,
                from_name=FROM_NAME,
                to_emails=[user.email],
                subject=subject,
                html_content=html,
            ),
            email_type=EmailType.MARKETING,
            user_id=str(user.id),
        )

    logger.info(
        "Educator broadcast queued",
        recipients=len(recipients),
        scope=scope,
        subject=subject,
    )

    if recipients:
        crud.event.create(
            db,
            title="Educator broadcast sent",
            description=f"'{subject}' queued to {len(recipients)} educators",
            info={"subject": subject, "scope": scope, "recipients": len(recipients)},
            account=account,
        )

    return len(recipients)


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

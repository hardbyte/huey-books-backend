from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from structlog import get_logger

from app.api.dependencies.security import get_current_active_superuser
from app.db.session import get_session
from app.schemas.broadcast import (
    BroadcastAudience,
    BroadcastPreview,
    BroadcastSendIn,
    BroadcastSendResult,
    BroadcastTestIn,
)
from app.services import broadcast as broadcast_service

logger = get_logger()

router = APIRouter(tags=["Broadcast"])
public_router = APIRouter(tags=["Public"])


@router.post(
    "/broadcast/preview",
    response_model=BroadcastPreview,
    dependencies=[Depends(get_current_active_superuser)],
)
def preview_broadcast(
    audience: BroadcastAudience,
    session: Session = Depends(get_session),
):
    """Count (and sample) the recipients a broadcast would reach. Staff only."""
    recipients = broadcast_service.resolve_recipients(session, audience)
    return BroadcastPreview(
        recipient_count=len(recipients),
        sample_names=[r.name for r in recipients[:5] if r.name],
    )


@router.post(
    "/broadcast",
    response_model=BroadcastSendResult,
)
def send_broadcast(
    data: BroadcastSendIn,
    account=Depends(get_current_active_superuser),
    session: Session = Depends(get_session),
):
    """Queue a broadcast email to the chosen audience. Staff only."""
    queued = broadcast_service.send_broadcast(
        session,
        subject=data.subject,
        body=data.body,
        audience=data.audience,
        account=account,
    )
    return BroadcastSendResult(queued=queued)


@router.post(
    "/broadcast/test",
    response_model=BroadcastSendResult,
)
def send_broadcast_test(
    data: BroadcastTestIn,
    account=Depends(get_current_active_superuser),
    session: Session = Depends(get_session),
):
    """Send the composed message only to the requesting staff member. Staff only."""
    queued = broadcast_service.send_test(
        session, subject=data.subject, body=data.body, account=account
    )
    return BroadcastSendResult(queued=queued)


def _page(body: str) -> str:
    return (
        "<html><body style='font-family:Arial,sans-serif;text-align:center;"
        f"padding:60px;color:#1f1f1f;'>{body}</body></html>"
    )


_UNSUB_OK = _page(
    "<h2>You've been unsubscribed</h2>"
    "<p>You won't receive further update emails from Huey Books.</p>"
)
_UNSUB_BAD = _page(
    "<h2>Link expired or invalid</h2>"
    "<p>Please contact hello@hueybooks.com to update your preferences.</p>"
)


@public_router.get("/email/unsubscribe", response_class=HTMLResponse)
def unsubscribe_page(token: str = Query(...)):
    """Show an unsubscribe confirmation.

    Deliberately does NOT change anything: email link scanners and clients
    pre-fetch GET links, which would otherwise unsubscribe people who never
    clicked. The actual opt-out happens on the POST below.
    """
    if broadcast_service.verify_unsubscribe_token(token) is None:
        return HTMLResponse(content=_UNSUB_BAD, status_code=400)
    form = _page(
        "<h2>Unsubscribe from Huey Books updates?</h2>"
        f"<form method='post' action='/v1/email/unsubscribe?token={token}'>"
        "<button type='submit' style='padding:10px 20px;font-size:15px;'>"
        "Yes, unsubscribe</button></form>"
    )
    return HTMLResponse(content=form, status_code=200)


@public_router.post("/email/unsubscribe", response_class=HTMLResponse)
def unsubscribe(
    token: str = Query(...),
    session: Session = Depends(get_session),
):
    """Perform the opt-out. Serves both the confirmation form above and RFC 8058
    one-click unsubscribe (mail clients POST here directly). Public (token-auth)."""
    ok = broadcast_service.unsubscribe_user(session, token)
    return HTMLResponse(
        content=_UNSUB_OK if ok else _UNSUB_BAD,
        status_code=200 if ok else 400,
    )

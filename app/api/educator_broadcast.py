from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from structlog import get_logger

from app.api.dependencies.security import get_current_active_superuser
from app.db.session import get_session
from app.schemas.educator_broadcast import (
    BroadcastPreview,
    BroadcastSendIn,
    BroadcastSendResult,
)
from app.services import educator_broadcast as broadcast_service

logger = get_logger()

router = APIRouter(tags=["Educators"])
public_router = APIRouter(tags=["Public"])


@router.get(
    "/educator-broadcast/preview",
    response_model=BroadcastPreview,
    dependencies=[Depends(get_current_active_superuser)],
)
def preview_educator_broadcast(
    scope: str = Query("all_educators"),
    school_id: Optional[UUID] = Query(None),
    session: Session = Depends(get_session),
):
    """Count (and sample) the recipients a broadcast would reach. Staff only."""
    recipients = broadcast_service.resolve_recipients(session, scope, school_id)
    return BroadcastPreview(
        recipient_count=len(recipients),
        sample_names=[r.name for r in recipients[:5] if r.name],
    )


@router.post(
    "/educator-broadcast",
    response_model=BroadcastSendResult,
)
def send_educator_broadcast(
    data: BroadcastSendIn,
    account=Depends(get_current_active_superuser),
    session: Session = Depends(get_session),
):
    """Queue a broadcast email to the chosen educator audience. Staff only."""
    queued = broadcast_service.send_broadcast(
        session,
        subject=data.subject,
        body=data.body,
        scope=data.audience.scope,
        school_id=data.audience.school_id,
        account=account,
    )
    return BroadcastSendResult(queued=queued)


_UNSUB_OK = (
    "<html><body style='font-family:Arial,sans-serif;text-align:center;"
    "padding:60px;color:#1f1f1f;'><h2>You've been unsubscribed</h2>"
    "<p>You won't receive further update emails from Huey Books.</p></body></html>"
)
_UNSUB_BAD = (
    "<html><body style='font-family:Arial,sans-serif;text-align:center;"
    "padding:60px;color:#1f1f1f;'><h2>Link expired or invalid</h2>"
    "<p>Please contact hello@hueybooks.com to update your preferences.</p>"
    "</body></html>"
)


@public_router.get("/email/unsubscribe", response_class=HTMLResponse)
def unsubscribe(
    token: str = Query(...),
    session: Session = Depends(get_session),
):
    """One-click unsubscribe target from broadcast emails. Public (token-auth)."""
    ok = broadcast_service.unsubscribe_user(session, token)
    return HTMLResponse(
        content=_UNSUB_OK if ok else _UNSUB_BAD,
        status_code=200 if ok else 400,
    )

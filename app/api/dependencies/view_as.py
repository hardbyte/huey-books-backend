from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import Depends, HTTPException, Request, Response
from jose import JWTError, jwt
from sqlalchemy.orm import Session
from structlog import get_logger

from app import crud
from app.config import get_settings
from app.db.session import get_session
from app.models.user import UserAccountType
from app.services.security import ALGORITHM, get_payload_from_access_token

logger = get_logger()
VIEW_AS_AUDIENCE = "wriveted-view-as"
VIEW_AS_ROLES = {UserAccountType.EDUCATOR, UserAccountType.SCHOOL_ADMIN}
READ_ROUTES = {
    "/organisations",
    "/organisations/{organisation_uuid}",
    "/organisations/{organisation_uuid}/members",
    "/libraries",
    "/libraries/{library_uuid}",
    "/libraries/{library_uuid}/collections",
    "/libraries/{library_uuid}/collections/{collection_uuid}/items",
    "/libraries/{library_uuid}/members",
    "/libraries/{library_uuid}/insights",
    "/libraries/{library_uuid}/review-queue",
    "/auth/me",
    "/school/{wriveted_identifier}",
    "/school/{school_uuid}/insights",
    "/school/{wriveted_identifier}/staff",
    "/collection/{collection_id}",
    "/collection/{collection_id}/items",
    "/collection/{collection_id}/info",
    "/collection/{collection_id}/{isbn}",
    "/works",
    "/work/{work_id}",
    "/work/{work_id}/reviews",
    "/work/{work_id}/labelling-prompt",
    "/review-queue",
    "/review-stats",
}


def create_view_as_context(actor, target) -> dict:
    if not target.is_active or target.type not in VIEW_AS_ROLES or not target.school_id:
        raise HTTPException(
            400, "Choose an active educator or school administrator with a school"
        )
    expires_at = datetime.now(UTC) + timedelta(minutes=15)
    context = jwt.encode(
        {
            "aud": VIEW_AS_AUDIENCE,
            "actor": str(actor.id),
            "target": str(target.id),
            "exp": expires_at,
        },
        get_settings().SECRET_KEY,
        algorithm=ALGORITHM,
    )
    logger.info("View as started", actor_id=str(actor.id), target_id=str(target.id))
    return {"context": context, "expires_at": expires_at, "target_name": target.name}


def enforce_view_as(
    request: Request, response: Response, db: Session = Depends(get_session)
):
    context = request.headers.get("X-View-As")
    response.headers["Vary"] = "X-View-As, Authorization"
    if context is not None or request.headers.get("Authorization"):
        response.headers["Cache-Control"] = "no-store"
    if context is None:
        return
    try:
        claims = jwt.decode(
            context,
            get_settings().SECRET_KEY,
            algorithms=[ALGORITHM],
            audience=VIEW_AS_AUDIENCE,
            options={"require_exp": True, "require_aud": True},
        )
        scheme, token = request.headers.get("Authorization", "").split(" ", 1)
        payload = get_payload_from_access_token(token)
        namespace, account_type, actor_id = payload.sub.lower().split(":")
        if (
            scheme.lower() != "bearer"
            or namespace != "wriveted"
            or account_type != "user-account"
        ):
            raise ValueError("Not a user credential")
        if UUID(actor_id) != UUID(claims["actor"]):
            raise ValueError("Actor mismatch")
        actor = crud.user.get(db, id=actor_id)
        target = crud.user.get(db, id=UUID(claims["target"]))
        if not actor or not actor.is_active or actor.type != UserAccountType.WRIVETED:
            raise ValueError("Staff permission required")
        if (
            not target
            or not target.is_active
            or target.type not in VIEW_AS_ROLES
            or not target.school_id
        ):
            raise ValueError("Target unavailable")
    except (JWTError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(
            403, "View as expired or unavailable. Exit View as and try again."
        ) from exc

    route = request.scope.get("route")
    path = getattr(route, "path", "")
    prefix = get_settings().API_V1_STR
    allowed = request.method == "GET" and path.removeprefix(prefix) in READ_ROUTES
    logger.info(
        "View as request",
        actor_id=str(actor.id),
        target_id=str(target.id),
        method=request.method,
        path=request.url.path,
        allowed=allowed,
    )
    if not allowed:
        raise HTTPException(
            403,
            "View as is read-only; this action is unavailable. Exit View as to continue.",
        )
    request.state.view_as_user = target

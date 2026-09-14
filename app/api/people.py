from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from app.api.common.workspace_errors import WorkspaceRoute
from app.api.dependencies.async_db_dep import DBSessionDep
from app.api.dependencies.security import get_current_active_user
from app.models.user import User
from app.schemas.people import (
    AccessSource,
    PeoplePage,
    PeopleRole,
    PeopleScope,
    PersonAdd,
    PersonChange,
)
from app.services import people
from app.services.email_notification import trigger_email_delivery_async

router = APIRouter(tags=["People and access"], route_class=WorkspaceRoute)
Actor = Annotated[User, Depends(get_current_active_user)]


@router.get("/{scope}/{scope_id}/people", response_model=PeoplePage)
async def list_people(
    scope: PeopleScope,
    scope_id: UUID,
    session: DBSessionDep,
    actor: Actor,
    response: Response,
    q: str = Query("", max_length=200),
    skip: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
):
    response.headers["Cache-Control"] = "private, no-store"
    return await people.list_people(session, actor, scope, scope_id, q, skip, limit)


@router.post("/{scope}/{scope_id}/people", status_code=204)
async def add_person(
    scope: PeopleScope,
    scope_id: UUID,
    data: PersonAdd,
    session: DBSessionDep,
    actor: Actor,
):
    await people.add_person(session, actor, scope, scope_id, data)
    await trigger_email_delivery_async()


@router.patch("/{scope}/{scope_id}/people/{user_id}/{source}", status_code=204)
async def change_person(
    scope: PeopleScope,
    scope_id: UUID,
    user_id: UUID,
    source: AccessSource,
    data: PersonChange,
    session: DBSessionDep,
    actor: Actor,
):
    await people.change_person(
        session, actor, scope, scope_id, user_id, source, data.expected_role, data.role
    )


@router.delete("/{scope}/{scope_id}/people/{user_id}/{source}", status_code=204)
async def remove_person(
    scope: PeopleScope,
    scope_id: UUID,
    user_id: UUID,
    source: AccessSource,
    expected_role: PeopleRole,
    session: DBSessionDep,
    actor: Actor,
):
    await people.change_person(
        session, actor, scope, scope_id, user_id, source, expected_role
    )


@router.post("/{scope}/{scope_id}/people/{user_id}/resend", status_code=204)
async def resend_invitation(
    scope: PeopleScope,
    scope_id: UUID,
    user_id: UUID,
    session: DBSessionDep,
    actor: Actor,
):
    await people.resend(session, actor, scope, scope_id, user_id)
    await trigger_email_delivery_async()

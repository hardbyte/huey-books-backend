from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.common.workspace_errors import WorkspaceRoute
from app.api.dependencies.async_db_dep import DBSessionDep
from app.api.dependencies.security import get_current_active_user
from app.models.user import User
from app.schemas.organisation import (
    CollectionCreate,
    CollectionImport,
    LibraryCreate,
    LibraryDetailsUpdate,
    LibrarySummary,
    MemberByEmail,
    MemberChange,
    OrganisationCreate,
    OrganisationDetail,
    OrganisationList,
    WorkspaceMemberList,
)
from app.services import organisation_management

router = APIRouter(tags=["Organisation workspaces"], route_class=WorkspaceRoute)
Actor = Annotated[User, Depends(get_current_active_user)]


@router.get("/organisations", response_model=OrganisationList)
async def list_organisations(
    session: DBSessionDep,
    actor: Actor,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
):
    return await organisation_management.list_organisations(
        session=session, actor=actor, skip=skip, limit=limit
    )


@router.post("/organisations", response_model=OrganisationDetail, status_code=201)
async def create_organisation(
    data: OrganisationCreate, session: DBSessionDep, actor: Actor
):
    return await organisation_management.create_organisation(
        data=data, session=session, actor=actor
    )


@router.get("/organisations/{organisation_uuid}", response_model=OrganisationDetail)
async def get_organisation(
    organisation_uuid: UUID, session: DBSessionDep, actor: Actor
):
    return await organisation_management.get_organisation(
        organisation_uuid=organisation_uuid, session=session, actor=actor
    )


@router.post(
    "/organisations/{organisation_uuid}/libraries",
    response_model=LibrarySummary,
    status_code=201,
)
async def create_library(
    organisation_uuid: UUID, data: LibraryCreate, session: DBSessionDep, actor: Actor
):
    return await organisation_management.create_library(
        organisation_uuid=organisation_uuid, data=data, session=session, actor=actor
    )


@router.post(
    "/organisations/{organisation_uuid}/libraries/{library_uuid}/attach",
    response_model=LibrarySummary,
)
async def attach_library(
    organisation_uuid: UUID, library_uuid: UUID, session: DBSessionDep, actor: Actor
):
    return await organisation_management.attach_library(
        organisation_uuid=organisation_uuid,
        library_uuid=library_uuid,
        session=session,
        actor=actor,
    )


@router.get("/libraries")
async def list_libraries(
    session: DBSessionDep,
    actor: Actor,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    q: str | None = Query(None, max_length=200),
    organisation_uuid: UUID | None = Query(None),
    standalone: bool = Query(False),
):
    return await organisation_management.list_libraries(
        session=session,
        actor=actor,
        skip=skip,
        limit=limit,
        q=q,
        organisation_uuid=organisation_uuid,
        standalone=standalone,
    )


@router.get("/libraries/{library_uuid}", response_model=LibrarySummary)
async def get_library(library_uuid: UUID, session: DBSessionDep, actor: Actor):
    return await organisation_management.get_library(
        library_uuid=library_uuid, session=session, actor=actor
    )


@router.patch("/libraries/{library_uuid}", response_model=LibrarySummary)
async def update_library_details(
    library_uuid: UUID, data: LibraryDetailsUpdate, session: DBSessionDep, actor: Actor
):
    return await organisation_management.update_library_details(
        library_uuid=library_uuid, data=data, session=session, actor=actor
    )


@router.get("/libraries/{library_uuid}/collections")
async def list_collections(library_uuid: UUID, session: DBSessionDep, actor: Actor):
    return await organisation_management.list_collections(
        library_uuid=library_uuid, session=session, actor=actor
    )


@router.post("/libraries/{library_uuid}/collections", status_code=201)
async def create_collection(
    library_uuid: UUID, data: CollectionCreate, session: DBSessionDep, actor: Actor
):
    return await organisation_management.create_collection(
        library_uuid=library_uuid, data=data, session=session, actor=actor
    )


@router.get("/libraries/{library_uuid}/collections/{collection_uuid}/items")
async def list_collection_items(
    library_uuid: UUID,
    collection_uuid: UUID,
    session: DBSessionDep,
    actor: Actor,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    q: str | None = Query(None, max_length=200),
):
    return await organisation_management.list_collection_items(
        library_uuid=library_uuid,
        collection_uuid=collection_uuid,
        session=session,
        actor=actor,
        skip=skip,
        limit=limit,
        q=q,
    )


@router.post("/libraries/{library_uuid}/collections/{collection_uuid}/import")
async def import_collection(
    library_uuid: UUID,
    collection_uuid: UUID,
    data: CollectionImport,
    session: DBSessionDep,
    actor: Actor,
):
    return await organisation_management.import_collection(
        library_uuid=library_uuid,
        collection_uuid=collection_uuid,
        data=data,
        session=session,
        actor=actor,
    )


@router.post("/organisations/{organisation_uuid}/members")
async def add_organisation_member(
    organisation_uuid: UUID, data: MemberByEmail, session: DBSessionDep, actor: Actor
):
    return await organisation_management.add_organisation_member(
        organisation_uuid=organisation_uuid, data=data, session=session, actor=actor
    )


@router.post("/libraries/{library_uuid}/members")
async def add_library_member(
    library_uuid: UUID, data: MemberByEmail, session: DBSessionDep, actor: Actor
):
    return await organisation_management.add_library_member(
        library_uuid=library_uuid, data=data, session=session, actor=actor
    )


@router.get(
    "/organisations/{organisation_uuid}/members", response_model=WorkspaceMemberList
)
async def list_organisation_members(
    organisation_uuid: UUID,
    session: DBSessionDep,
    actor: Actor,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
):
    return await organisation_management.list_organisation_members(
        organisation_uuid=organisation_uuid,
        session=session,
        actor=actor,
        skip=skip,
        limit=limit,
    )


@router.put("/organisations/{organisation_uuid}/members/{user_uuid}")
async def put_organisation_member(
    organisation_uuid: UUID,
    user_uuid: UUID,
    data: MemberChange,
    session: DBSessionDep,
    actor: Actor,
):
    return await organisation_management.put_organisation_member(
        organisation_uuid=organisation_uuid,
        user_uuid=user_uuid,
        data=data,
        session=session,
        actor=actor,
    )


@router.delete(
    "/organisations/{organisation_uuid}/members/{user_uuid}", status_code=204
)
async def delete_organisation_member(
    organisation_uuid: UUID, user_uuid: UUID, session: DBSessionDep, actor: Actor
):
    return await organisation_management.delete_organisation_member(
        organisation_uuid=organisation_uuid,
        user_uuid=user_uuid,
        session=session,
        actor=actor,
    )


@router.get("/libraries/{library_uuid}/members", response_model=WorkspaceMemberList)
async def list_library_members(
    library_uuid: UUID,
    session: DBSessionDep,
    actor: Actor,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
):
    return await organisation_management.list_library_members(
        library_uuid=library_uuid, session=session, actor=actor, skip=skip, limit=limit
    )


@router.put("/libraries/{library_uuid}/members/{user_uuid}")
async def put_library_member(
    library_uuid: UUID,
    user_uuid: UUID,
    data: MemberChange,
    session: DBSessionDep,
    actor: Actor,
):
    return await organisation_management.put_library_member(
        library_uuid=library_uuid,
        user_uuid=user_uuid,
        data=data,
        session=session,
        actor=actor,
    )


@router.delete("/libraries/{library_uuid}/members/{user_uuid}", status_code=204)
async def delete_library_member(
    library_uuid: UUID, user_uuid: UUID, session: DBSessionDep, actor: Actor
):
    return await organisation_management.delete_library_member(
        library_uuid=library_uuid, user_uuid=user_uuid, session=session, actor=actor
    )

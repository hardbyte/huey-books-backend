import datetime
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Security
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette import status
from structlog import get_logger

from app import crud
from app.api.common.pagination import PaginatedQueryParams
from app.api.dependencies.security import (
    create_user_access_token,
    get_active_principals,
    get_current_active_superuser_or_backend_service_account,
    get_current_active_user_or_service_account,
)
from app.api.dependencies.user import get_user_from_id
from app.db.session import get_session
from app.models.subscription import SubscriptionType
from app.models.user import User, UserAccountType
from app.permissions import Permission
from app.schemas.auth import SpecificUserDetail
from app.schemas.pagination import Pagination
from app.schemas.users.user_create import UserCreateIn
from app.schemas.users.user_list import UserListsResponse
from app.schemas.users.user_update import InternalUserUpdateIn, UserUpdateIn
from app.services.users import handle_user_creation

logger = get_logger()

router = APIRouter(
    tags=["Users"],
    dependencies=[Depends(get_current_active_user_or_service_account)],
)


@router.get(
    "/users",
    response_model=UserListsResponse,
    dependencies=[Security(get_current_active_superuser_or_backend_service_account)],
)
async def get_users(
    q: Optional[str] = Query(None, description="Filter users by name or email"),
    is_active: Optional[bool] = Query(
        None, description="Return active or inactive users. Default is all."
    ),
    type: Optional[UserAccountType] = Query(
        None, description="Filter users by account type. Default is all."
    ),
    active_subscription_type: Optional[SubscriptionType] = Query(
        None, description="Filter users by active subscription type. Default is all."
    ),
    pagination: PaginatedQueryParams = Depends(),
    session: Session = Depends(get_session),
):
    """
    List all users with optional filters.
    """
    logger.info("Listing users", type=type)
    total_matching_query, page_of_users = crud.user.get_filtered_with_count(
        db=session,
        query_string=q,
        is_active=is_active,
        type=type,
        active_subscription_type=active_subscription_type,
        skip=pagination.skip,
        limit=pagination.limit,
    )
    logger.info("Fetching related data to populate user briefs", type=type)

    return UserListsResponse(
        data=page_of_users,
        pagination=Pagination(**pagination.to_dict(), total=total_matching_query),
    )


@router.post(
    "/user",
    response_model=SpecificUserDetail,
    dependencies=[Security(get_current_active_superuser_or_backend_service_account)],
)
async def create_user(
    user_data: UserCreateIn,
    generate_pathway_lists: bool = False,
    session: Session = Depends(get_session),
):
    """
    Admin endpoint for creating new users.
    If a provided user is a `reader` type and `generate_pathway_lists` is true, will also create booklists
    `Books to read now` and `Books to read next`, populating each with 10 appropriate books based on the `huey_attributes`
    blob in `user_data`.
    Will create `reader` users as children for any `parent` types, if provided.
    """
    logger.debug("Creating a user", data=user_data)
    try:
        new_user = handle_user_creation(session, user_data, generate_pathway_lists)
        return new_user

    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except IntegrityError:
        raise HTTPException(status_code=409, detail="Email already exists")


@router.get("/user/{user_id}", response_model=SpecificUserDetail)
async def get_user(user: User = Permission("details", get_user_from_id)):
    logger.info("Retrieving details on one user", user=user)
    logger.info(f"Type of user is {user.type}")
    return user


@router.patch("/user/{user_id}", response_model=SpecificUserDetail)
async def update_user(
    user_update: UserUpdateIn,
    merge_dicts: bool = Query(
        default=False,
        description="Whether or not to *merge* the data in info dict, i.e. if adding new or updating existing individual fields (but want to keep previous data)",
    ),
    session: Session = Depends(get_session),
    user: User = Permission("update", get_user_from_id),
    principals=Depends(get_active_principals),
):
    if user_update.type == UserAccountType.WRIVETED and "role:admin" not in principals:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Insufficient privileges to modify that account type.",
        )

    try:
        updated_items = user_update.dict(exclude_unset=True)
        update_data = InternalUserUpdateIn(current_type=user.type, **updated_items)
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=json.loads(e.json()),
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    logger.info("Updating a user")

    updated_user = crud.user.update(
        session, db_obj=user, obj_in=update_data, merge_dicts=merge_dicts
    )
    return updated_user


@router.post(
    "/user/{user_id}/auth-token",
    responses={
        401: {"description": "Unauthorized"},
        422: {"description": "Invalid data"},
    },
)
def magic_link_endpoint(
    user=Permission("update", get_user_from_id),
):
    """
    Create a Wriveted API magic-link token for a user.
    """
    logger.info("Generating magic link access-token for user", user=user)
    wriveted_access_token = create_user_access_token(
        user, expires_delta=datetime.timedelta(days=90)
    )
    return {
        "access_token": wriveted_access_token,
        "token_type": "bearer",
    }


@router.delete("/user/{uuid}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_user(
    uuid: str,
    purge: bool = False,
    account=Depends(get_current_active_superuser_or_backend_service_account),
    session: Session = Depends(get_session),
):
    """
    Mark user INACTIVE.

    If `purge` is `True` we instead delete the user entirely from the database.
    Note the user can then sign up again and a purge will delete all associated events.
    """
    user = crud.user.get(db=session, id=uuid)
    logger.info("Request to delete a user", user_to_delete=user, account=account)

    crud.event.create(
        title="User account deleted",
        description=f"User {user.name} marked inactive by {account}",
        account=account,
        session=session,
    )
    user.is_active = False
    session.flush()

    if purge:
        logger.info("Trying to purge user", user=user)
        session.delete(user)

    session.commit()

from typing import List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query, Security
from fastapi_permissions import Allow, Authenticated, has_permission
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette import status
from structlog import get_logger

from app import crud
from app.api.common.pagination import PaginatedQueryParams
from app.api.dependencies.booklist import (
    get_booklist_from_wriveted_id,
    get_public_huey_booklist_from_slug,
)
from app.api.dependencies.security import (
    get_active_principals,
    get_current_active_user_or_service_account,
)
from app.db.session import get_session
from app.models import BookList, ServiceAccount, User
from app.models.booklist import ListSharingType, ListType
from app.models.educator import Educator
from app.models.school_admin import SchoolAdmin
from app.permissions import Permission
from app.repositories.booklist_repository import booklist_repository
from app.repositories.event_repository import event_repository
from app.repositories.school_repository import school_repository
from app.schemas.booklist import (
    BookListBrief,
    BookListCreateIn,
    BookListDetail,
    BookListDetailEnriched,
    BookListsResponse,
    BookListUpdateIn,
)
from app.schemas.pagination import Pagination
from app.services.booklists import populate_booklist_object, validate_booklist_publicity

logger = get_logger()

router = APIRouter(
    tags=["Booklists"],
    dependencies=[Security(get_current_active_user_or_service_account)],
)

public_router = APIRouter(tags=["Booklists"])

"""
Bulk access control rules apply to calling the create and get lists endpoints.
Further access control is applied inside each endpoint.
"""
bulk_booklist_access_control_list = [
    (Allow, Authenticated, "create"),
    (Allow, Authenticated, "read"),
    (Allow, Authenticated, "update"),
    (Allow, "role:admin", "delete"),
]


@router.post(
    "/list",
    dependencies=[
        Permission("create", bulk_booklist_access_control_list),
    ],
    response_model=BookListBrief,
)
def add_booklist(
    booklist: BookListCreateIn,
    account: Union[User, ServiceAccount] = Depends(
        get_current_active_user_or_service_account
    ),
    principals: List = Depends(get_active_principals),
    session: Session = Depends(get_session),
):
    logger.info("Creating a book list", account=account, type=booklist.type)
    logger.debug("Checking permissions", principals=principals)
    if (
        booklist.type in {ListType.OTHER_LIST, ListType.HUEY, ListType.REGION}
        and "role:admin" not in principals
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admin users may create that type of book list",
        )

    try:
        validate_booklist_publicity(booklist)
    except ValueError as e:
        raise HTTPException(
            status_code=422,
            detail=str(e),
        )

    if booklist.type == ListType.SCHOOL:
        if booklist.school_id is not None:
            logger.debug(
                "Caller supplied a school id, swapping out the wriveted identifier for the db id"
            )
            school_orm = school_repository.get_by_wriveted_id_or_404(
                db=session, wriveted_id=booklist.school_id
            )
        else:
            logger.debug("Seeing if the caller is clearly associated with *one* school")
            if (
                isinstance(account, (Educator, SchoolAdmin))
                and account.school_id is not None
            ):
                school_orm = school_repository.get_by_id_or_404(
                    session, id=account.school_id
                )
            elif isinstance(account, ServiceAccount) and len(account.schools) == 1:
                school_orm = account.schools[0]
            else:
                logger.debug("Couldn't identify one school from given information")
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="Need to know which school to use",
                )
        if not has_permission(principals, "update", school_orm):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only school admin accounts may create school book lists",
            )
        booklist.school_id = school_orm.id
    elif booklist.type == ListType.PERSONAL:
        logger.debug("Asked to create personal book list")
        if booklist.user_id is not None:
            # Ensure that account is allowed to update mentioned user
            target_user = crud.user.get_or_404(db=session, id=booklist.user_id)
            if not has_permission(principals, "update", target_user):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You lack the permission to create book lists for other users",
                )
        else:
            if isinstance(account, User):
                booklist.user_id = account.id
            else:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="Only user accounts may create book lists without specifying the user",
                )

    try:
        if booklist.items is None:
            logger.info("Creating an empty book list")
            booklist.items = []

        booklist_orm_object = booklist_repository.create(
            db=session, obj_in=booklist, commit=True
        )

        event_repository.create(
            session=session,
            title="Booklist created",
            description=f"{account.name} created booklist '{booklist.name}'",
            info={
                "type": booklist.type,
                "id": str(booklist_orm_object.id),
            },
            account=account,
        )
        return booklist_orm_object

    except IntegrityError as e:
        logger.warning("Database integrity error while adding booklist", exc_info=e)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Couldn't create booklist",
        )


@router.get(
    "/lists",
    response_model=BookListsResponse,
)
def get_booklists(
    list_type: Optional[ListType] = None,
    sharing_type: Optional[ListSharingType] = None,
    pagination: PaginatedQueryParams = Depends(),
    principals: List = Depends(get_active_principals),
    session: Session = Depends(get_session),
):
    """
    Retrieve a filtered list of Book Lists.

    ### Access Control

    Admin accounts can see and edit all book lists.
    School staff can see and edit all School lists.
    Everyone can see Huey and Regional lists.
    Authenticated users can see their own personal lists.
    """
    logger.debug(
        "Getting list of booklists", list_type=list_type, pagination=pagination
    )
    booklists_query = booklist_repository.get_all_query_with_optional_filters(
        db=session, list_type=list_type, sharing_type=sharing_type
    )

    booklists = [
        booklist
        for booklist in session.scalars(
            booklist_repository.apply_pagination(
                query=booklists_query, skip=pagination.skip, limit=pagination.limit
            )
        ).all()
        if has_permission(principals, "read", booklist)
    ]

    booklist_count = booklist_repository.count_query(db=session, query=booklists_query)

    return BookListsResponse(
        pagination=Pagination(**pagination.to_dict(), total=booklist_count),
        data=booklists,
    )


@router.get(
    "/list/{booklist_identifier}",
    response_model=(BookListDetailEnriched | BookListDetail),
)
def get_booklist_detail(
    enriched: bool = Query(
        default=False,
        title="Enrich items in response",
        description="""
            If enabled, will include fully-enriched editions and labels for each booklist item
            i.e. including cover image, summary, and other metadata that may be desired for displaying each book. 
            Will grab a suitable edition if none is specified in the booklist item.
        """,
    ),
    booklist: BookList = Permission("read", get_booklist_from_wriveted_id),
    pagination: PaginatedQueryParams = Depends(),
    session: Session = Depends(get_session),
):
    return populate_booklist_object(booklist, session, pagination, enriched)


@router.patch(
    "/list/{booklist_identifier}",
    response_model=BookListBrief,
    responses={422: {"description": "Unprocessable request"}},
)
def update_booklist(
    changes: BookListUpdateIn,
    booklist: BookList = Permission("update", get_booklist_from_wriveted_id),
    session: Session = Depends(get_session),
    principals: List = Depends(get_active_principals),
):
    """
    Update a booklist.

    Can be used to alter the list metadata as well as to add, edit and remove items from the list.
    """
    logger.debug("Updating booklist", booklist=booklist)

    logger.debug("Checking permissions", principals=principals)
    if (
        booklist.type in {ListType.OTHER_LIST, ListType.HUEY, ListType.REGION}
        and "role:admin" not in principals
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admin users may create that type of book list",
        )

    try:
        validate_booklist_publicity(changes, booklist)
    except ValueError as e:
        raise HTTPException(
            status_code=422,
            detail=str(e),
        )

    try:
        updated_booklist = booklist_repository.update(
            db=session, db_obj=booklist, obj_in=changes
        )
    except IntegrityError as e:
        logger.warning("Couldn't alter the booklist", exc_info=e)
        raise HTTPException(status_code=422, detail="Booklist update wasn't possible")

    return updated_booklist


@router.delete(
    "/list/{booklist_identifier}",
    response_model=BookListBrief,
)
def delete_booklist(
    booklist: BookList = Permission("delete", get_booklist_from_wriveted_id),
    session: Session = Depends(get_session),
):
    """
    Delete a booklist

    """
    logger.debug("Removing a booklist", booklist=booklist)
    booklist_repository.remove(db=session, id=booklist.id)
    return booklist


@public_router.get(
    "/public-lists",
    response_model=BookListsResponse,
)
def get_public_booklists(
    pagination: PaginatedQueryParams = Depends(),
    session: Session = Depends(get_session),
):
    """
    Retrieve a paginated list of Public Huey Book Lists.
    Requires no authentication.
    """
    booklists_query = booklist_repository.get_all_query_with_optional_filters(
        db=session, list_type=ListType.HUEY, sharing_type=ListSharingType.PUBLIC
    )

    booklists = session.scalars(
        booklist_repository.apply_pagination(
            query=booklists_query, skip=pagination.skip, limit=pagination.limit
        )
    ).all()

    booklist_count = booklist_repository.count_query(db=session, query=booklists_query)

    return BookListsResponse(
        pagination=Pagination(**pagination.to_dict(), total=booklist_count),
        data=booklists,
    )


@public_router.get(
    "/public-list/{booklist_slug}",
    response_model=(BookListDetailEnriched | BookListDetail),
)
def get_public_booklist_detail(
    enriched: bool = Query(
        default=False,
        title="Enrich items in response",
        description="""
            If enabled, will include fully-enriched editions and labels for each booklist item
            i.e. including cover image, summary, and other metadata that may be desired for displaying each book. 
            Will grab a suitable edition if none is specified in the booklist item.
        """,
    ),
    booklist: BookList = Depends(get_public_huey_booklist_from_slug),
    pagination: PaginatedQueryParams = Depends(),
    session: Session = Depends(get_session),
):
    """
    Retrieve a public booklist.
    """
    return populate_booklist_object(booklist, session, pagination, enriched)

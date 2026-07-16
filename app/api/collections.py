from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Security
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from structlog import get_logger

from app.api.common.pagination import PaginatedQueryParams
from app.api.dependencies.async_db_dep import DBSessionDep
from app.api.dependencies.booklist import get_booklist_from_wriveted_id
from app.api.dependencies.collection import (
    get_collection_from_id,
    get_collection_item_from_body,
    get_collection_item_from_id,
    validate_collection_creation,
)
from app.api.dependencies.editions import get_edition_from_isbn
from app.api.dependencies.security import get_current_active_user_or_service_account
from app.api.dependencies.user import (
    get_and_validate_collection_with_optional_reader,
    get_reader_from_body,
)
from app.db.session import get_session
from app.models import BookList, CollectionItem, Edition
from app.models.collection import Collection
from app.models.collection_item_activity import CollectionItemReadStatus
from app.permissions import Permission
from app.repositories.collection_item_activity_repository import (
    collection_item_activity_repository,
)
from app.repositories.event_repository import event_repository
from app.schemas.booklist_collection_intersection import (
    BookListItemInCollection,
    CollectionBookListIntersection,
)
from app.schemas.collection import (
    CollectionAndItemsUpdateIn,
    CollectionBrief,
    CollectionCreateIn,
    CollectionInfo,
    CollectionItemActivityBase,
    CollectionItemActivityBrief,
    CollectionItemAndStatusCreateIn,
    CollectionItemCreateIn,
    CollectionItemDetail,
    CollectionItemsResponse,
    CollectionUpdateSummaryResponse,
)
from app.schemas.pagination import Pagination
from app.services.account_refs import get_account_ref
from app.services.background_events import record_booklist_collection_comparison_event
from app.services.collection_service import CollectionService
from app.services.collections import (
    get_collection_info_with_criteria,
    get_collection_items_also_in_booklist,
)
from app.services.events import create_event

logger = get_logger()

router = APIRouter(
    tags=["Book Collection"],
    dependencies=[
        # Shouldn't be necessary
        Security(get_current_active_user_or_service_account)
    ],
)


def get_collection_service() -> CollectionService:
    return CollectionService()


@router.get(
    "/collection/{collection_id}",
    response_model=CollectionBrief,
)
async def get_collection_details(
    collection: Collection = Permission("read", get_collection_from_id),
):
    """
    Get a summary of an existing collection.
    """
    return collection


@router.get(
    "/collection/{collection_id}/items",
    response_model=CollectionItemsResponse,
)
async def get_collection_items(
    collection: Collection = Permission("read", get_collection_from_id),
    query: Optional[str] = Query(None, description="Query string for edition title"),
    reader_id: str | None = Query(
        None, description="Filter by items that a specific Reader has interacted with"
    ),
    read_status: Optional[CollectionItemReadStatus] = Query(
        None,
        description="Filter by a specific -current- CollectionItemActivity read status, for any Reader (if no reader specified). Example: retrieve all items that are -currently- being read by at least one Reader.",
    ),
    pagination: PaginatedQueryParams = Depends(),
    session: Session = Depends(get_session),
):
    """
    Retrieve items in a collection, with filtering and pagination.
    """
    logger.debug("Getting collection items", pagination=pagination)

    service = get_collection_service()
    matching_count, items = service.list_items(
        session,
        collection_id=collection.id,
        query=query,
        reader_id=reader_id,
        read_status=read_status,
        skip=pagination.skip,
        limit=pagination.limit,
    )

    logger.debug(
        "Loading collection items",
        items_matching_query=matching_count,
        items_returned=len(items),
    )
    # Note the serializing is fast
    return CollectionItemsResponse(
        data=items,
        pagination=Pagination(**pagination.to_dict(), total=matching_count),
    )


@router.get(
    "/collection/{collection_id}/info",
    response_model=CollectionInfo,
)
async def get_collection_info(
    session: DBSessionDep,
    collection: Collection = Permission("read", get_collection_from_id),
):
    """
    Endpoint returning information about how much of the collection is labeled.
    """
    logger.debug("Getting collection info")

    return await get_collection_info_with_criteria(session, collection.id)


# note the order of the endpoints in this file is important:
# to avoid ambiguity the /items and /info endpoints must be defined before this /{isbn} endpoint
@router.get(
    "/collection/{collection_id}/{isbn}",
    response_model=CollectionItemDetail,
)
async def get_collection_item_by_isbn(
    collection: Collection = Permission("read", get_collection_from_id),
    edition: Edition = Depends(get_edition_from_isbn),
    session: Session = Depends(get_session),
):
    """
    Returns a selected item from a collection, raising a 404 if it doesn't exist (either in the collection or at all).
    """
    logger.debug(
        f"Searching collection {collection.id} for edition {edition.isbn}",
    )

    collection_item = session.execute(
        select(CollectionItem)
        .where(CollectionItem.collection_id == collection.id)
        .where(CollectionItem.edition_isbn == edition.isbn)
    ).scalar_one_or_none()

    if collection_item is None:
        raise HTTPException(
            status_code=404,
            detail=f"Collection {collection.id} does not contain edition {edition.isbn}",
        )

    return collection_item


@router.post(
    "/collection",
    response_model=CollectionBrief,
    dependencies=[Depends(validate_collection_creation)],
)
async def create_collection(
    collection_data: CollectionCreateIn,
    session: Session = Depends(get_session),
    ignore_conflicts: bool = Query(
        default=True,
        description="""Whether or not to ignore duplicate entries in the collection. Note: only one copy of an edition can be held in a collection - 
        this parameter simply controls whether or not an error is raised if a duplicate is found""",
    ),
):
    """
    Endpoint for creating a new collection, provided a collection isn't already assigned to the target user or school
    """
    logger.debug("Creating collection")
    try:
        service = get_collection_service()
        return service.create_collection(
            session, data=collection_data, ignore_conflicts=ignore_conflicts
        )
    except IntegrityError:
        raise HTTPException(
            status_code=409,
            detail="Duplicate entries in collection - use ignore_conflicts to ignore this error",
        )


@router.delete("/collection/{collection_id}")
async def delete_collection(
    collection: Collection = Permission("delete", get_collection_from_id),
    session: Session = Depends(get_session),
):
    """
    Endpoint to delete a collection.
    """
    logger.debug("Deleting collection")
    service = get_collection_service()
    service.delete_collection(session, collection=collection)
    return {"message": "Collection deleted"}


@router.put(
    "/collection/{collection_id}",
    response_model=CollectionBrief,
)
async def set_collection(
    collection_data: CollectionCreateIn,
    collection: Collection = Permission("delete", get_collection_from_id),
    session: Session = Depends(get_session),
    ignore_conflicts: bool = Query(
        default=True,
        description="""Whether or not to ignore duplicate entries in the collection. Note: only one copy of an edition can be held in a collection - 
        this parameter simply controls whether or not an error is raised if a duplicate is found""",
    ),
):
    """
    Endpoint for replacing an existing collection and its items.
    """
    logger.debug("Deleting collection")
    logger.debug("Replacing deleted collection")
    try:
        service = get_collection_service()
        return service.replace_collection(
            session,
            existing=collection,
            data=collection_data,
            ignore_conflicts=ignore_conflicts,
        )
    except IntegrityError:
        raise HTTPException(
            status_code=409,
            detail="Duplicate entries in collection - use ignore_conflicts to ignore this error",
        )


@router.get(
    "/collection/{collection_id}/compare-with-booklist/{booklist_identifier}",
    response_model=CollectionBookListIntersection,
)
async def get_collection_booklist_intersection(
    background_tasks: BackgroundTasks,
    collection: Collection = Permission("read", get_collection_from_id),
    booklist: BookList = Permission("read", get_booklist_from_wriveted_id),
    pagination: PaginatedQueryParams = Depends(),
    session: Session = Depends(get_session),
    account=Depends(get_current_active_user_or_service_account),
):
    """
    Endpoint returning information about which items in a booklist are part of a collection.

    Pagination applies to the booklist
    """
    logger.debug(
        "Computing booklist collection intersection",
        collection=collection,
        booklist=booklist,
    )

    paginated_booklist_item_query = booklist.items.statement.offset(
        pagination.skip
    ).limit(pagination.limit)

    common_collection_items = await get_collection_items_also_in_booklist(
        session,
        collection,
        paginated_booklist_item_query,
    )
    common_work_ids = {item.work_id for item in common_collection_items}

    account_type, account_id = get_account_ref(account)
    background_tasks.add_task(
        record_booklist_collection_comparison_event,
        str(collection.id),
        str(booklist.id),
        len(common_collection_items),
        account_type,
        account_id,
    )

    return CollectionBookListIntersection(
        pagination=Pagination(**pagination.to_dict(), total=booklist.book_count),
        data=[
            BookListItemInCollection(
                in_collection=booklist_item.work_id in common_work_ids,
                work_id=booklist_item.work_id,
                work_brief=booklist_item.work,
                editions_in_collection=[
                    collection_item.edition_isbn
                    for collection_item in common_collection_items
                    if collection_item.work_id == booklist_item.work_id
                ],
            )
            for booklist_item in session.scalars(paginated_booklist_item_query)
        ],
    )


@router.post(
    "/collection/{collection_id}/item",
    response_model=CollectionItemDetail,
)
async def add_collection_item(
    data: CollectionItemAndStatusCreateIn,
    collection: Collection = Permission(
        "update", get_and_validate_collection_with_optional_reader
    ),
    session: Session = Depends(get_session),
):
    """
    Endpoint for adding a new item to a collection.
    """
    logger.debug("Adding item to collection", collection=collection)

    service = get_collection_service()
    try:
        return service.add_collection_item(session, collection=collection, item=data)
    except IntegrityError:
        raise HTTPException(
            status_code=409,
            detail=f"ISBN {data.edition_isbn} already in collection",
        )


@router.put(
    "/collection/{collection_id}/items",
    response_model=CollectionUpdateSummaryResponse,
)
async def set_collection_items(
    collection_data: List[CollectionItemCreateIn],
    collection: Collection = Permission("update", get_collection_from_id),
    account=Depends(get_current_active_user_or_service_account),
    session: Session = Depends(get_session),
):
    """
    Set the contents of a collection.

    Requires only an ISBN to identify each Edition, other attributes are optional. Note all editions will be stored as
    part of the collection, but as additional data is fetched from partner APIs it can take up to a few days before
    the editions are fully "hydrated".
    """
    logger.info(
        "Resetting an entire collection",
        collection=collection,
        account=account,
    )
    logger.info(
        f"Adding/syncing {len(collection_data)} ISBNs with collection",
        collection=collection,
        account=account,
    )
    service = get_collection_service()
    return await service.set_collection_items(
        session, collection=collection, items=collection_data, account=account
    )


@router.patch(
    "/collection/{collection_id}",
    response_model=CollectionUpdateSummaryResponse,
)
async def update_collection(
    collection_update_data: CollectionAndItemsUpdateIn,
    collection: Collection = Permission("update", get_collection_from_id),
    account=Depends(get_current_active_user_or_service_account),
    session: Session = Depends(get_session),
    merge_dicts: bool = Query(
        default=False,
        description="Whether or not to *merge* the data in info dict, i.e. if adding new or updating existing individual fields (but want to keep previous data)",
    ),
    ignore_conflicts: bool = Query(
        default=True,
        description="""Whether or not to ignore duplicate entries in the collection. Note: only one copy of an edition can be held in a collection - 
        this parameter simply controls whether or not an error is raised if a duplicate is found""",
    ),
):
    """
    Update a collection itself, and/or its items with a list of changes.

    Changes can be to add, remove, or update editions of books. Many
    changes of different types can be added in a single call to this
    API, they must however all refer to one collection.

    If the edition are already in the Wriveted database an `"action"`
    and identifier is required. The identifier can be either `isbn` or
    `id` (the Wriveted internal Collection Item ID). For example adding
    known editions to a collection can be achieved with entries of this
    form:

    ```json
    { "edition_isbn": "978...", "action": "add" }
    ```

    Existing collection items can be referred to by their ISBN, or by
    their `id`.

    Note: any unknown editions will be created as unhydrated, empty objects in the db.
    To provide metadata for new books, please use the `POST /editions` endpoint.

    ### Action Types

    - `add`
    - `remove`
    - `update` - e.g. used to change the `copies_total` and `copies_available`

    """
    logger.info("Updating collection", collection=collection, account=account)

    try:
        service = get_collection_service()
        await service.update_collection(
            session=session,
            collection=collection,
            account=account,
            changes=collection_update_data,
            merge_dicts=merge_dicts,
            ignore_conflicts=ignore_conflicts,
        )

        event_repository.create(
            session=session,
            title="Collection Update",
            description="Updates made to collection",
            info={
                "collection_id": str(collection.id),
            },
            school=collection.school,
            account=account,
            commit=False,
        )
    except IntegrityError:
        raise HTTPException(
            status_code=409,
            detail="Duplicate entries in collection - use ignore_conflicts to ignore this error",
        )

    logger.debug("Checking size of collection after changes applied")
    session.commit()
    count = session.execute(
        select(func.count(CollectionItem.id)).where(
            CollectionItem.collection_id == collection.id
        )
    ).scalar_one()

    return {"msg": "updated", "collection_size": count}


@router.get(
    "/collection-item/{collection_item_id}",
    response_model=CollectionItemDetail,
)
async def get_collection_item(
    collection_item: CollectionItem = Permission("read", get_collection_item_from_id),
):
    """
    Get a single collection item.
    """
    return collection_item


@router.post(
    "/collection-item-activity",
    response_model=CollectionItemActivityBrief,
    dependencies=[
        Permission("update", get_reader_from_body),
        Permission("update", get_collection_item_from_body),
    ],
)
async def log_collection_item_activity(
    data: CollectionItemActivityBase,
    session: Session = Depends(get_session),
):
    """
    Create new activity entry for a collection item and reader
    """
    activity = collection_item_activity_repository.create(db=session, obj_in=data)

    create_event(
        session=session,
        title="Collection item activity created",
        description=f"Collection item activity '{activity.status}' created for collection item {activity.collection_item_id}",
        info={
            "collection_id": str(activity.collection_item.collection_id),
            "collection_item_id": str(activity.collection_item_id),
            "activity_id": str(activity.id),
        },
        account=activity.reader,
    )

    return activity

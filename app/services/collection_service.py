from __future__ import annotations

from typing import List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from structlog import get_logger

from app import crud
from app.models.collection import Collection
from app.models.collection_item import CollectionItem
from app.models.collection_item_activity import CollectionItemReadStatus
from app.repositories.collection_item_activity_repository import (
    collection_item_activity_repository,
)
from app.repositories.collection_repository import collection_repository
from app.schemas.collection import (
    CollectionAndItemsUpdateIn,
    CollectionCreateIn,
    CollectionItemActivityBase,
    CollectionItemAndStatusCreateIn,
    CollectionItemCreateIn,
)
from app.services.collection_errors import (
    CollectionOwnerChangeError,
    DefaultCollectionInUseError,
)
from app.services.collections import (
    add_editions_to_collection_by_isbn,
    reset_collection,
)
from app.services.collections import update_collection as svc_update_collection

logger = get_logger()


class CollectionService:
    """
    Service layer for collection operations.

    Orchestrates CRUD helpers and SQL with clear transaction points.
    """

    # Reads
    def list_items(
        self,
        session: Session,
        *,
        collection_id,
        query: Optional[str],
        reader_id: Optional[str],
        read_status: Optional[CollectionItemReadStatus],
        skip: int,
        limit: int,
    ) -> Tuple[int, List[CollectionItem]]:
        return crud.collection.get_filtered_with_count(
            db=session,
            collection_id=collection_id,
            query_string=query,
            reader_id=reader_id,
            read_status=read_status,
            skip=skip,
            limit=limit,
        )

    # Writes
    def create_collection(
        self,
        session: Session,
        *,
        data: CollectionCreateIn,
        ignore_conflicts: bool,
    ) -> Collection:
        created = crud.collection.create(
            session, obj_in=data, commit=True, ignore_conflicts=ignore_conflicts
        )
        return created

    def replace_collection(
        self,
        session: Session,
        *,
        existing: Collection,
        data: CollectionCreateIn,
        ignore_conflicts: bool,
    ) -> Collection:
        if data.school_id != existing.school_id or data.user_id != existing.user_id:
            raise CollectionOwnerChangeError("A collection's owner cannot be changed.")
        crud.collection.delete_all_items(db=session, db_obj=existing, commit=False)
        existing.name = data.name
        existing.info = data.info
        for item in data.items or []:
            crud.collection.add_item_to_collection(
                db=session,
                collection_orm_object=existing,
                item=item,
                commit=False,
                ignore_conflicts=ignore_conflicts,
            )
        session.commit()
        session.refresh(existing)
        return existing

    def delete_collection(self, session: Session, *, collection: Collection) -> None:
        if collection.school_id is not None:
            collection_repository.lock_library(session, collection.school_id)
        if collection.is_default and collection_repository.has_other_collections(
            session, collection
        ):
            raise DefaultCollectionInUseError(
                "Remove the additional collections before deleting the default collection."
            )
        collection_repository.delete_by_id(session, collection.id)
        session.commit()

    def add_collection_item(
        self,
        session: Session,
        *,
        collection: Collection,
        item: CollectionItemAndStatusCreateIn,
    ) -> CollectionItem:
        read_status = item.read_status
        reader_id = item.reader_id
        item_data = CollectionItemCreateIn(
            edition_isbn=item.edition_isbn,
            copies_total=item.copies_total,
            copies_available=item.copies_available,
            info=item.info,
        )
        item_id = crud.collection.add_item_to_collection(
            session, item=item_data, collection_orm_object=collection
        )
        obj = session.get(CollectionItem, item_id)

        if read_status or reader_id:
            collection_item_activity_repository.create(
                session,
                obj_in=CollectionItemActivityBase(
                    collection_item_id=obj.id,
                    status=read_status,
                    reader_id=str(reader_id) if reader_id else None,
                ),
            )
        session.commit()
        return obj

    async def set_collection_items(
        self,
        session,
        *,
        collection: Collection,
        items: List[CollectionItemCreateIn],
        account,
    ) -> dict:
        reset_collection(session, collection, account)
        if items:
            await add_editions_to_collection_by_isbn(
                session, items, collection, account
            )

        count = session.execute(
            select(func.count(CollectionItem.id)).where(
                CollectionItem.collection == collection
            )
        ).scalar_one()
        return {
            "msg": f"Collection set. Total editions: {count}",
            "collection_size": count,
        }

    async def update_collection(
        self,
        session,
        *,
        collection: Collection,
        account,
        changes: CollectionAndItemsUpdateIn,
        merge_dicts: bool,
        ignore_conflicts: bool,
    ) -> Collection:
        updated = await svc_update_collection(
            session=session,
            collection=collection,
            account=account,
            obj_in=changes,
            merge_dicts=merge_dicts,
            ignore_conflicts=ignore_conflicts,
        )
        return updated

"""
Collection repository - domain-focused data access for Collection domain.

Replaces the generic CRUDCollection class with proper repository pattern.
"""

from abc import ABC, abstractmethod
from typing import Optional, Sequence, Tuple
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import asc, delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_upsert
from sqlalchemy.exc import IntegrityError, NoResultFound
from sqlalchemy.orm import Session, aliased, contains_eager, raiseload
from structlog import get_logger

from app.models import Author, Edition, Work
from app.models.collection import Collection
from app.models.collection_item import CollectionItem
from app.models.collection_item_activity import (
    CollectionItemActivity,
    CollectionItemReadStatus,
)
from app.repositories.edition_repository import edition_repository
from app.schemas import is_url
from app.schemas.collection import (
    CollectionAndItemsUpdateIn,
    CollectionCreateIn,
    CollectionItemCreateIn,
    CollectionItemUpdate,
    CollectionUpdateType,
)
from app.services.cover_images import (
    handle_collection_item_cover_image_update,
    handle_new_collection_item_cover_image,
)
from app.services.editions import get_definitive_isbn
from app.utils.dict_utils import deep_merge_dicts

logger = get_logger()


class CollectionRepository(ABC):
    """Repository interface for Collection domain operations."""

    @abstractmethod
    def get(self, db: Session, id: UUID) -> Optional[Collection]:
        """Get a collection by its primary key ID."""
        pass

    @abstractmethod
    def get_by_id_or_404(self, db: Session, id: UUID) -> Collection:
        """Get a collection by ID or raise 404."""
        pass

    @abstractmethod
    def create(
        self,
        db: Session,
        obj_in: CollectionCreateIn,
        commit: bool = True,
        ignore_conflicts: bool = False,
    ) -> Collection:
        """Create a new collection with items."""
        pass

    @abstractmethod
    def get_or_create(
        self, db: Session, collection_data: CollectionCreateIn, commit: bool = True
    ) -> Tuple[Collection, bool]:
        """Get a collection by school or user id, creating if needed."""
        pass

    @abstractmethod
    def update(
        self,
        db: Session,
        db_obj: Collection,
        obj_in: CollectionAndItemsUpdateIn,
        merge_dicts: bool = True,
        commit: bool = True,
        ignore_conflicts: bool = False,
    ) -> Collection:
        """Update a collection and its items."""
        pass

    @abstractmethod
    def delete_all_items(
        self, db: Session, db_obj: Collection, commit: bool = True
    ) -> Collection:
        """Delete all items from a collection."""
        pass

    @abstractmethod
    def remove(self, db: Session, db_obj: Collection) -> Collection:
        """Delete a collection."""
        pass

    @abstractmethod
    def add_item_to_collection(
        self,
        db: Session,
        collection_orm_object: Collection,
        item: CollectionItemUpdate | CollectionItemCreateIn,
        commit: bool = True,
        ignore_conflicts: bool = False,
    ):
        """Add a single item to a collection."""
        pass

    @abstractmethod
    def add_items_to_collection(
        self,
        db: Session,
        collection_orm_object: Collection,
        items: list[CollectionItemUpdate | CollectionItemCreateIn],
        commit: bool = True,
        preserve_existing: bool = False,
    ):
        """Add multiple items to a collection."""
        pass

    @abstractmethod
    def get_collection_items_by_collection_id(
        self, db: Session, collection_id: UUID
    ) -> Sequence[CollectionItem]:
        """Get all items for a collection."""
        pass

    @abstractmethod
    def get_collection_item_by_collection_id_and_isbn(
        self, db: Session, collection_id: UUID, isbn: str
    ) -> Optional[CollectionItem]:
        """Get a collection item by collection ID and ISBN."""
        pass

    @abstractmethod
    def get_collection_item(
        self, db: Session, collection_item_id: int
    ) -> Optional[CollectionItem]:
        """Get a collection item by ID."""
        pass

    @abstractmethod
    def get_collection_item_or_404(
        self, db: Session, collection_item_id: int
    ) -> CollectionItem:
        """Get a collection item by ID or raise 404."""
        pass

    @abstractmethod
    def get_filtered_with_count(
        self,
        db: Session,
        collection_id: UUID,
        query_string: Optional[str] = None,
        reader_id: Optional[UUID] = None,
        read_status: Optional[CollectionItemReadStatus] = None,
        skip: int = 0,
        limit: int = 1000,
    ) -> Tuple[int, Sequence[CollectionItem]]:
        """Get filtered collection items with total count."""
        pass

    @abstractmethod
    def apply_pagination(self, query, skip: int = 0, limit: int = 100):
        """Apply pagination to a query."""
        pass


class CollectionRepositoryImpl(CollectionRepository):
    """Implementation of CollectionRepository."""

    def get(self, db: Session, id: UUID) -> Optional[Collection]:
        """Get a collection by its primary key ID."""
        return db.get(Collection, id)

    def get_by_id_or_404(self, db: Session, id: UUID) -> Collection:
        """Get a collection by ID or raise 404."""
        query = select(Collection).where(Collection.id == id)
        try:
            return db.execute(query).scalar_one()
        except NoResultFound:
            raise HTTPException(
                status_code=404, detail=f"Collection with id {id} not found."
            )

    def create(
        self,
        db: Session,
        obj_in: CollectionCreateIn,
        commit: bool = True,
        ignore_conflicts: bool = False,
    ) -> Collection:
        """Create a new collection with items."""
        items = obj_in.items or []
        obj_in.items = []

        # Create the base collection object
        collection_data = obj_in.model_dump(exclude_unset=True, exclude={"items"})
        collection_orm_object = Collection(**collection_data)
        db.add(collection_orm_object)
        db.flush()

        logger.debug(
            "Collection entry created in database",
            collection_id=str(collection_orm_object.id),
        )

        logger.debug(
            f"Adding {len(items)} collection items to collection",
            collection_id=str(collection_orm_object.id),
        )
        for item in items:
            self.add_item_to_collection(
                db=db,
                collection_orm_object=collection_orm_object,
                item=item,
                commit=False,
                ignore_conflicts=ignore_conflicts,
            )

        if commit:
            db.commit()
            db.refresh(collection_orm_object)

        return collection_orm_object

    def get_or_create(
        self, db: Session, collection_data: CollectionCreateIn, commit: bool = True
    ) -> Tuple[Collection, bool]:
        """Get a collection by school or user id, creating if needed."""
        if collection_data.user_id:
            q = select(Collection).where(Collection.user_id == collection_data.user_id)
        else:
            q = select(Collection).where(
                Collection.school_id == collection_data.school_id
            )
        try:
            collection = db.execute(q).scalar_one()
            return collection, False
        except NoResultFound:
            logger.info(
                "Creating new collection",
                user_id=(
                    str(collection_data.user_id) if collection_data.user_id else None
                ),
                school_id=(
                    str(collection_data.school_id)
                    if collection_data.school_id
                    else None
                ),
                name=collection_data.name,
                num_items=len(collection_data.items or []),
            )
            collection = self.create(db, obj_in=collection_data, commit=commit)
            return collection, True

    def update(
        self,
        db: Session,
        db_obj: Collection,
        obj_in: CollectionAndItemsUpdateIn,
        merge_dicts: bool = True,
        commit: bool = True,
        ignore_conflicts: bool = False,
    ) -> Collection:
        """Update a collection and its items."""
        item_changes = getattr(obj_in, "items", [])
        if item_changes is None:
            item_changes = []

        if hasattr(obj_in, "items"):
            del obj_in.items

        logger.debug("Updating the base collection object")
        update_data = obj_in.model_dump(exclude_unset=True, exclude={"items"})

        for field, value in update_data.items():
            if merge_dicts and field == "info" and hasattr(db_obj, "info"):
                current_info = db_obj.info or {}
                new_info = value or {}
                deep_merge_dicts(current_info, new_info)
                setattr(db_obj, field, current_info)
            else:
                setattr(db_obj, field, value)

        logger.debug("Flushing the collection object")
        db.flush()

        logger.info(
            f"Applying {len(item_changes)} item changes",
            collection_id=str(db_obj.id),
            collection_name=db_obj.name,
        )
        summary_counts = {"added": 0, "removed": 0, "updated": 0}

        for change in item_changes:
            match change.action:
                case CollectionUpdateType.ADD:
                    try:
                        self.add_item_to_collection(
                            db=db,
                            collection_orm_object=db_obj,
                            item=change,
                            commit=False,
                            ignore_conflicts=ignore_conflicts,
                        )
                        summary_counts["added"] += 1
                    except IntegrityError as e:
                        raise e
                case CollectionUpdateType.UPDATE:
                    self._update_item_in_collection(
                        db=db,
                        collection_id=db_obj.id,
                        item_update=change,
                        commit=False,
                    )
                    summary_counts["updated"] += 1
                case CollectionUpdateType.REMOVE:
                    self._remove_item_from_collection(
                        db=db,
                        collection_orm_object=db_obj,
                        item_to_remove=change,
                        commit=False,
                    )
                    summary_counts["removed"] += 1

        logger.debug("Processed all collection items")
        db_obj.updated_at = text("DEFAULT")
        logger.debug("Flushing changes to DB")

        db.flush()
        if commit:
            logger.debug(
                "Committing changes",
                collection_id=str(db_obj.id),
                collection_name=db_obj.name,
            )
            db.commit()

        return db_obj

    def delete_all_items(
        self, db: Session, db_obj: Collection, commit: bool = True
    ) -> Collection:
        """Delete all items from a collection."""
        db.execute(
            delete(CollectionItem).where(CollectionItem.collection_id == db_obj.id)
        )

        if commit:
            db.commit()
            db.refresh(db_obj)

        return db_obj

    def remove(self, db: Session, db_obj: Collection) -> Collection:
        """Delete a collection."""
        db.delete(db_obj)
        db.commit()
        return db_obj

    def _update_item_in_collection(
        self,
        db: Session,
        collection_id: UUID,
        item_update: CollectionItemUpdate | CollectionItemCreateIn,
        commit: bool = True,
    ):
        """Internal method to update a single item in a collection."""
        update_query = update(CollectionItem).where(
            CollectionItem.collection_id == collection_id
        )
        select_query = select(CollectionItem).where(
            CollectionItem.collection_id == collection_id
        )
        if item_update.id is not None:
            update_query = update_query.where(CollectionItem.id == item_update.id)
            select_query = select_query.where(CollectionItem.id == item_update.id)
        else:
            update_query = update_query.where(
                CollectionItem.edition_isbn == item_update.edition_isbn
            )
            select_query = select_query.where(
                CollectionItem.edition_isbn == item_update.edition_isbn
            )

        if item_update.info is not None:
            logger.debug("Updating info for collection item")
            item_orm_object = db.scalar(select_query)
            if item_orm_object is None:
                logger.warning("Skipping update of info for missing item in collection")
                return
            info_dict = dict(item_orm_object.info)
            info_update_dict = item_update.info.model_dump(exclude_unset=True)

            if image_data := info_update_dict.get("cover_image"):
                logger.debug(
                    "Updating cover image for collection item",
                    collection_item_id=item_orm_object.id,
                )
                if is_url(image_data):
                    info_update_dict["cover_image"] = image_data
                else:
                    info_update_dict["cover_image"] = (
                        handle_collection_item_cover_image_update(
                            item_orm_object,
                            image_data,
                        )
                    )

            deep_merge_dicts(info_dict, info_update_dict)
            item_orm_object.info = info_dict

        update_data = {}
        if item_update.copies_available:
            update_data["copies_available"] = item_update.copies_available

        if item_update.copies_total:
            update_data["copies_total"] = item_update.copies_total

        db.execute(update_query.values(update_data))

        if commit:
            logger.debug("Committing item update")
            db.commit()

    def _remove_item_from_collection(
        self,
        db: Session,
        collection_orm_object: Collection,
        item_to_remove: CollectionItemUpdate | CollectionItemCreateIn,
        commit: bool = True,
    ):
        """Internal method to remove a single item from a collection."""
        base_query = delete(CollectionItem).where(
            CollectionItem.collection_id == collection_orm_object.id
        )
        if item_to_remove.id is not None:
            query = base_query.where(CollectionItem.id == item_to_remove.id)
        else:
            query = base_query.where(
                CollectionItem.edition_isbn == item_to_remove.edition_isbn
            )

        db.execute(query)

        if commit:
            db.commit()
            db.refresh(collection_orm_object)

    def add_items_to_collection(
        self,
        db: Session,
        collection_orm_object: Collection,
        items: list[CollectionItemUpdate | CollectionItemCreateIn],
        commit: bool = True,
        preserve_existing: bool = False,
    ):
        """Add multiple items to a collection."""
        item_data = [
            {
                "collection_id": collection_orm_object.id,
                "edition_isbn": item.edition_isbn,
                "copies_available": item.copies_available or 1,
                "copies_total": item.copies_total or 1,
                "info": (
                    item.info.model_dump(mode="json", exclude_unset=True)
                    if item.info is not None
                    else {}
                ),
            }
            for item in items
        ]
        stmt = pg_upsert(CollectionItem)
        stmt = (
            stmt.on_conflict_do_nothing(
                constraint="uq_collection_items_collection_id_edition_isbn",
            )
            if preserve_existing
            else stmt.on_conflict_do_update(
                constraint="uq_collection_items_collection_id_edition_isbn",
                set_={
                    "copies_available": stmt.excluded.copies_available,
                    "copies_total": stmt.excluded.copies_total,
                    "info": CollectionItem.info.concat(stmt.excluded.info),
                },
            )
        )

        try:
            changed_ids = (
                db.execute(stmt.returning(CollectionItem.id), item_data).scalars().all()
            )
        except IntegrityError as e:
            logger.warning("Integrity Error while replacing collection")
            raise e

        if commit:
            db.commit()

        return len(changed_ids)

    def add_item_to_collection(
        self,
        db: Session,
        collection_orm_object: Collection,
        item: CollectionItemUpdate | CollectionItemCreateIn,
        commit: bool = True,
        ignore_conflicts: bool = False,
    ):
        """Add a single item to a collection."""
        isbn = item.edition_isbn
        if isbn is not None:
            try:
                edition = edition_repository.get_or_create_unhydrated(
                    db=db, isbn=isbn, commit=True
                )
                isbn = edition.isbn
            except AssertionError:
                logger.warning("Skipping invalid isbn", isbn=isbn)
                return

        info_dict = {}
        if item.info is not None:
            info_dict = item.info.model_dump(exclude_unset=True)

            if cover_image_data := info_dict.get("cover_image"):
                logger.debug("Processing cover image for new collection item")
                if is_url(cover_image_data):
                    info_dict["cover_image"] = cover_image_data
                else:
                    info_dict["cover_image"] = handle_new_collection_item_cover_image(
                        str(collection_orm_object.id),
                        isbn,
                        info_dict["cover_image"],
                    )

        new_orm_item_data = dict(
            collection_id=str(collection_orm_object.id),
            edition_isbn=isbn,
            copies_available=item.copies_available or 1,
            copies_total=item.copies_total or 1,
            info=info_dict,
        )

        stmt = (
            pg_upsert(CollectionItem).on_conflict_do_update(
                constraint="uq_collection_items_collection_id_edition_isbn",
                set_=new_orm_item_data,
            )
            if ignore_conflicts
            else pg_upsert(CollectionItem)
        )

        try:
            result = db.execute(
                stmt.returning(CollectionItem.id),
                [new_orm_item_data],
            )
            new_id = result.scalar()

        except IntegrityError as e:
            raise IntegrityError(
                statement=f"Isbn {isbn} already exists in collection",
                params={},
                orig=e,
            ) from None

        if not isbn and collection_orm_object.school is not None:
            logger.warning(
                f"Item with no isbn added to a school collection #{collection_orm_object.id}"
            )
            from app import crud

            crud.event.create(
                session=db,
                level="warning",
                title="Unknown Book added to Collection",
                description=f"Book with no isbn added to collection #{collection_orm_object.id}",
                info={
                    "collection_id": str(collection_orm_object.id),
                    "title": item.info.title if item.info else None,
                    "author": item.info.author if item.info else None,
                    "item_id": str(new_id),
                },
                school=collection_orm_object.school,
                account=collection_orm_object.user,
                commit=False,
            )

        if commit:
            db.commit()

        return new_id

    def get_collection_items_by_collection_id(
        self, db: Session, collection_id: UUID
    ) -> Sequence[CollectionItem]:
        """Get all items for a collection."""
        return (
            db.execute(
                select(CollectionItem).where(
                    CollectionItem.collection_id == collection_id
                )
            )
            .scalars()
            .all()
        )

    def get_collection_item_by_collection_id_and_isbn(
        self, db: Session, collection_id: UUID, isbn: str
    ) -> Optional[CollectionItem]:
        """Get a collection item by collection ID and ISBN."""
        return db.execute(
            select(CollectionItem)
            .where(CollectionItem.collection_id == collection_id)
            .where(CollectionItem.edition_isbn == get_definitive_isbn(isbn))
        ).scalar_one_or_none()

    def get_collection_item(
        self, db: Session, collection_item_id: int
    ) -> Optional[CollectionItem]:
        """Get a collection item by ID."""
        return db.execute(
            select(CollectionItem).where(CollectionItem.id == collection_item_id)
        ).scalar_one_or_none()

    def get_collection_item_or_404(
        self, db: Session, collection_item_id: int
    ) -> CollectionItem:
        """Get a collection item by ID or raise 404."""
        try:
            return db.execute(
                select(CollectionItem).where(CollectionItem.id == collection_item_id)
            ).scalar_one()
        except NoResultFound:
            raise HTTPException(
                status_code=404,
                detail=f"Collection Item with id {collection_item_id} not found.",
            )

    def get_filtered_with_count(
        self,
        db: Session,
        collection_id: UUID,
        query_string: Optional[str] = None,
        reader_id: Optional[UUID] = None,
        read_status: Optional[CollectionItemReadStatus] = None,
        skip: int = 0,
        limit: int = 1000,
    ) -> Tuple[int, Sequence[CollectionItem]]:
        """Get filtered collection items with total count."""
        statement = (
            select(CollectionItem)
            .join(CollectionItem.edition, isouter=True)
            .join(Edition.work, isouter=True)
            .options(
                contains_eager(CollectionItem.edition).lazyload(Edition.illustrators),
                contains_eager(CollectionItem.edition).lazyload(Edition.collections),
                contains_eager(CollectionItem.edition).defer(Edition.collection_count),
                contains_eager(CollectionItem.edition)
                .contains_eager(Edition.work)
                .raiseload(Work.labelset),
                contains_eager(CollectionItem.edition)
                .contains_eager(Edition.work)
                .raiseload(Work.booklists),
                contains_eager(CollectionItem.edition)
                .contains_eager(Edition.work)
                .raiseload(Work.series),
                contains_eager(CollectionItem.edition)
                .contains_eager(Edition.work)
                .selectinload(Work.authors)
                .raiseload(Author.books),
                raiseload(CollectionItem.collection),
                raiseload(CollectionItem.activity_log),
            )
            .where(CollectionItem.collection_id == collection_id)
            .order_by(asc(Edition.title))
        )

        if query_string is not None:
            statement = statement.where(Edition.title.match(query_string))

        if reader_id is not None or read_status is not None:
            statement = statement.join(CollectionItemActivity)

            if reader_id is not None:
                statement = statement.where(
                    CollectionItemActivity.reader_id == reader_id
                )

            if read_status is not None:
                most_recent_timestamps = (
                    select(
                        CollectionItemActivity.collection_item_id,
                        CollectionItemActivity.reader_id,
                        func.max(CollectionItemActivity.timestamp).label(
                            "most_recent_timestamp"
                        ),
                    )
                    .group_by(
                        CollectionItemActivity.collection_item_id,
                        CollectionItemActivity.reader_id,
                    )
                    .alias("most_recent_timestamps")
                )

                statement = (
                    statement.where(
                        CollectionItem.id == CollectionItemActivity.collection_item_id
                    )
                    .where(
                        most_recent_timestamps.c.most_recent_timestamp
                        == CollectionItemActivity.timestamp
                    )
                    .where(
                        CollectionItemActivity.reader_id
                        == most_recent_timestamps.c.reader_id
                    )
                    .where(CollectionItemActivity.status == read_status)
                )

        cte = statement.cte()
        aliased_model = aliased(CollectionItem, cte)
        count_query = select(func.count(aliased_model.id))
        matching_count = db.scalar(count_query)

        paginated_items_query = self.apply_pagination(statement, skip=skip, limit=limit)

        return matching_count, db.scalars(paginated_items_query).all()

    def apply_pagination(self, query, skip: int = 0, limit: int = 100):
        """Apply pagination to a query."""
        return query.offset(skip).limit(limit)


# Singleton instance
collection_repository = CollectionRepositoryImpl()

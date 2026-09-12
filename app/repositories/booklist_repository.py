"""
Booklist repository - domain-focused data access for Booklist domain.

Replaces the generic CRUDBookList class with proper repository pattern.
"""

from abc import ABC, abstractmethod
from typing import Mapping, Optional, Sequence
from uuid import UUID

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, aliased, defer, joinedload, raiseload, selectinload
from structlog import get_logger

from app.models import Edition, LabelSet, School, User, Work
from app.models.booklist import BookList
from app.models.booklist_work_association import BookListItem
from app.schemas import is_url
from app.schemas.booklist import (
    BookListCreateIn,
    BookListItemUpdateIn,
    BookListUpdateIn,
    ItemUpdateType,
)
from app.utils.dict_utils import deep_merge_dicts

logger = get_logger()


class BooklistRepository(ABC):
    """Repository interface for Booklist domain operations."""

    @abstractmethod
    def get_by_id(self, db: Session, booklist_id: int) -> Optional[BookList]:
        """Get a booklist by its ID."""
        pass

    @abstractmethod
    def get_or_404(self, db: Session, id: int) -> BookList:
        """Get a booklist by ID or raise 404."""
        pass

    @abstractmethod
    def create(
        self, db: Session, obj_in: BookListCreateIn, commit: bool = True
    ) -> BookList:
        """Create a booklist with items and image handling."""
        pass

    @abstractmethod
    async def acreate(
        self, db: AsyncSession, obj_in: BookListCreateIn, commit: bool = True
    ) -> BookList:
        """Async version of create."""
        pass

    @abstractmethod
    def get_all_query_with_optional_filters(
        self,
        db: Session,
        list_type: Optional[str] = None,
        sharing_type: Optional[str] = None,
        school: Optional[School] = None,
        user: Optional[User] = None,
        query_string: Optional[str] = None,
    ):
        """Get a filtered query for booklists."""
        pass

    @abstractmethod
    def update(
        self, db: Session, db_obj: BookList, obj_in: BookListUpdateIn
    ) -> BookList:
        """Update a booklist and its items."""
        pass

    @abstractmethod
    def apply_pagination(self, query, skip: int = 0, limit: int = 100):
        """Apply pagination to a query."""
        pass

    @abstractmethod
    def count_query(self, db: Session, query) -> int:
        """Count the number of results in a query."""
        pass

    @abstractmethod
    def _update_item_in_booklist(
        self, db: Session, booklist_id: int, item_update: BookListItemUpdateIn
    ):
        """Update an item in a booklist."""
        pass

    @abstractmethod
    def _remove_item_from_booklist(
        self,
        db: Session,
        booklist_orm_object: BookList,
        item_to_remove: BookListItemUpdateIn,
    ):
        """Remove an item from a booklist."""
        pass

    @abstractmethod
    def _add_item_to_booklist(
        self,
        db: Session,
        booklist_orm_object: BookList,
        item_update: BookListItemUpdateIn,
    ):
        """Add an item to a booklist."""
        pass

    @abstractmethod
    async def _aadd_item_to_booklist(
        self,
        db: AsyncSession,
        booklist_orm_object: BookList,
        item_update: BookListItemUpdateIn,
    ):
        """Async version of _add_item_to_booklist."""
        pass

    @abstractmethod
    def remove(self, db: Session, id: int) -> BookList:
        """Delete a booklist by ID."""
        pass


class BooklistRepositoryImpl(BooklistRepository):
    """Implementation of BooklistRepository."""

    def get_detail_items(
        self, db: Session, booklist_id: UUID, *, skip: int, limit: int
    ) -> list[BookListItem]:
        statement = (
            select(BookListItem)
            .where(BookListItem.booklist_id == booklist_id)
            .offset(skip)
            .limit(limit)
            .order_by(BookListItem.order_id)
            .options(
                raiseload("*"),
                joinedload(BookListItem.work).options(
                    raiseload("*"),
                    selectinload(Work.authors),
                    selectinload(Work.labelset).options(
                        raiseload("*"),
                        selectinload(LabelSet.hues),
                        selectinload(LabelSet.reading_abilities),
                    ),
                ),
            )
        )
        return list(db.scalars(statement).all())

    def get_detail_editions(
        self, db: Session, work_ids: Sequence[int], requested_isbns: Mapping[int, str]
    ) -> dict[int, Edition]:
        edition_options = (
            raiseload("*"),
            defer(Edition.collection_count, raiseload=True),
            joinedload(Edition.work).options(
                raiseload("*"), selectinload(Work.authors)
            ),
            selectinload(Edition.illustrators),
        )
        requested = (
            {
                edition.isbn: edition
                for edition in db.scalars(
                    select(Edition)
                    .where(Edition.isbn.in_(set(requested_isbns.values())))
                    .options(*edition_options)
                ).all()
            }
            if requested_isbns
            else {}
        )
        result = {
            work_id: requested[isbn]
            for work_id, isbn in requested_isbns.items()
            if isbn in requested
        }
        fallback_work_ids = [work_id for work_id in work_ids if work_id not in result]
        if fallback_work_ids:
            fallback = db.scalars(
                select(Edition)
                .where(Edition.work_id.in_(fallback_work_ids))
                .distinct(Edition.work_id)
                .order_by(
                    Edition.work_id,
                    func.nullif(func.btrim(Edition.cover_url), "").is_not(None).desc(),
                    Edition.date_published.desc().nulls_last(),
                    Edition.isbn,
                )
                .options(*edition_options)
            ).all()
            result.update({edition.work_id: edition for edition in fallback})
        return result

    def get_alternative_covers(
        self, db: Session, work_ids: Sequence[int]
    ) -> dict[int, tuple[str, str]]:
        if not work_ids:
            return {}
        rows = db.execute(
            select(Edition.work_id, Edition.isbn, Edition.cover_url)
            .where(
                Edition.work_id.in_(set(work_ids)),
                func.nullif(func.btrim(Edition.cover_url), "").is_not(None),
            )
            .distinct(Edition.work_id)
            .order_by(
                Edition.work_id,
                Edition.date_published.desc().nulls_last(),
                Edition.isbn,
            )
        )
        return {work_id: (isbn, url.strip()) for work_id, isbn, url in rows}

    def get_by_id(self, db: Session, booklist_id: int) -> Optional[BookList]:
        """Get a booklist by its ID."""
        return db.get(BookList, booklist_id)

    def get_or_404(self, db: Session, id: int) -> BookList:
        """Get a booklist by ID or raise 404."""
        from fastapi import HTTPException

        booklist = self.get_by_id(db, id)
        if not booklist:
            raise HTTPException(
                status_code=404, detail=f"Booklist with id {id} not found"
            )
        return booklist

    def create(
        self, db: Session, obj_in: BookListCreateIn, commit: bool = True
    ) -> BookList:
        """Create a booklist with items and image handling."""
        items = obj_in.items
        obj_in.items = []

        image_url_data = None
        if obj_in.info and obj_in.info.image_url:
            image_url_data = obj_in.info.image_url
            del obj_in.info.image_url

        booklist_orm_object = BookList(**obj_in.model_dump())
        db.add(booklist_orm_object)
        if commit:
            db.commit()
            db.refresh(booklist_orm_object)

        logger.debug(
            "Booklist entry created in database", booklist_id=booklist_orm_object.id
        )

        if image_url_data:
            from app.services.booklists import handle_new_booklist_feature_image

            image_url = (
                image_url_data
                if is_url(image_url_data)
                else handle_new_booklist_feature_image(
                    booklist_id=str(booklist_orm_object.id),
                    image_url_data=image_url_data,
                )
            )
            if image_url:
                booklist_orm_object.info = deep_merge_dicts(
                    booklist_orm_object.info, {"image_url": image_url}
                )
                db.commit()

        for item in items:
            self._add_item_to_booklist(
                db=db,
                booklist_orm_object=booklist_orm_object,
                item_update=BookListItemUpdateIn(
                    action=ItemUpdateType.ADD, **item.model_dump()
                ),
            )

        logger.debug("Refreshed booklist count", count=booklist_orm_object.book_count)
        return booklist_orm_object

    async def acreate(
        self, db: AsyncSession, obj_in: BookListCreateIn, commit: bool = True
    ) -> BookList:
        """Async version of create."""
        items = obj_in.items
        obj_in.items = []

        image_url_data = None
        if obj_in.info and obj_in.info.image_url:
            image_url_data = obj_in.info.image_url
            del obj_in.info.image_url

        booklist_orm_object = BookList(**obj_in.model_dump())
        db.add(booklist_orm_object)
        if commit:
            await db.commit()
            await db.refresh(booklist_orm_object)

        logger.debug(
            "Booklist entry created in database", booklist_id=booklist_orm_object.id
        )

        if image_url_data:
            from app.services.booklists import handle_new_booklist_feature_image

            image_url = (
                image_url_data
                if is_url(image_url_data)
                else handle_new_booklist_feature_image(
                    booklist_id=str(booklist_orm_object.id),
                    image_url_data=image_url_data,
                )
            )
            if image_url:
                booklist_orm_object.info = deep_merge_dicts(
                    booklist_orm_object.info, {"image_url": image_url}
                )
                await db.commit()

        for item in items:
            await self._aadd_item_to_booklist(
                db=db,
                booklist_orm_object=booklist_orm_object,
                item_update=BookListItemUpdateIn(
                    action=ItemUpdateType.ADD, **item.model_dump()
                ),
            )

        logger.debug("Refreshed booklist count", count=booklist_orm_object.book_count)
        return booklist_orm_object

    def get_all_query_with_optional_filters(
        self,
        db: Session,
        list_type: Optional[str] = None,
        sharing_type: Optional[str] = None,
        school: Optional[School] = None,
        user: Optional[User] = None,
        query_string: Optional[str] = None,
    ):
        """Get a filtered query for booklists."""
        booklists_query = select(BookList).order_by(BookList.created_at.desc())

        if list_type is not None:
            booklists_query = booklists_query.where(BookList.type == list_type)
        if sharing_type is not None:
            booklists_query = booklists_query.where(BookList.sharing == sharing_type)
        if school is not None:
            booklists_query = booklists_query.where(BookList.school == school)
        if user is not None:
            booklists_query = booklists_query.where(BookList.user == user)
        if query_string is not None:
            booklists_query = booklists_query.where(
                func.lower(BookList.name).contains(query_string.lower())
            )

        return booklists_query

    def update(
        self, db: Session, db_obj: BookList, obj_in: BookListUpdateIn
    ) -> BookList:
        """Update a booklist and its items."""
        item_changes = obj_in.items if obj_in.items is not None else []
        del obj_in.items

        if obj_in.info and "image_url" in obj_in.info.model_dump(exclude_unset=True):
            from app.services.booklists import handle_booklist_feature_image_update

            obj_in.info.image_url = (
                obj_in.info.image_url
                if is_url(obj_in.info.image_url)
                else handle_booklist_feature_image_update(
                    booklist=db_obj, image_data=obj_in.info.image_url
                )
            )

        update_data = obj_in.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(db_obj, field, value)

        db.add(db_obj)

        for change in item_changes:
            match change.action:
                case ItemUpdateType.ADD:
                    self._add_item_to_booklist(
                        db=db, booklist_orm_object=db_obj, item_update=change
                    )
                case ItemUpdateType.UPDATE:
                    self._update_item_in_booklist(
                        db=db, booklist_id=db_obj.id, item_update=change
                    )
                case ItemUpdateType.REMOVE:
                    self._remove_item_from_booklist(
                        db=db, booklist_orm_object=db_obj, item_to_remove=change
                    )

        db.commit()
        db.refresh(db_obj)
        return db_obj

    def apply_pagination(self, query, skip: int = 0, limit: int = 100):
        """Apply pagination to a query."""
        return query.offset(skip).limit(limit)

    def count_query(self, db: Session, query) -> int:
        """Count the number of results in a query."""
        cte = query.cte()
        aliased_booklist = aliased(BookList, cte)
        return db.scalar(select(func.count(aliased_booklist.id)))

    def _update_item_in_booklist(
        self, db: Session, booklist_id: int, item_update: BookListItemUpdateIn
    ):
        """Update an item in a booklist."""
        item_orm_object = db.scalar(
            select(BookListItem)
            .where(BookListItem.booklist_id == booklist_id)
            .where(BookListItem.work_id == item_update.work_id)
        )
        if item_orm_object is None:
            logger.warning("Skipping update of missing item in booklist")
            return

        if item_update.order_id is not None:
            old_position = item_orm_object.order_id
            db.execute(text("SET CONSTRAINTS ALL DEFERRED"))

            new_position = item_update.order_id
            if new_position < old_position:
                stmt = (
                    update(BookListItem)
                    .where(BookListItem.booklist_id == booklist_id)
                    .where(BookListItem.order_id >= new_position)
                    .where(BookListItem.order_id < old_position)
                    .values(order_id=BookListItem.order_id + 1)
                )
            else:
                stmt = (
                    update(BookListItem)
                    .where(BookListItem.booklist_id == booklist_id)
                    .where(BookListItem.order_id > old_position)
                    .where(BookListItem.order_id <= new_position)
                    .values(order_id=BookListItem.order_id - 1)
                )
            db.execute(stmt)
            item_orm_object.order_id = item_update.order_id

        if item_update.info is not None:
            info_dict = dict(item_orm_object.info)
            update_dict = dict(item_update.info)
            deep_merge_dicts(info_dict, update_dict)
            item_orm_object.info = info_dict

        db.add(item_orm_object)
        db.commit()
        db.refresh(item_orm_object)

    def _remove_item_from_booklist(
        self,
        db: Session,
        booklist_orm_object: BookList,
        item_to_remove: BookListItemUpdateIn,
    ):
        """Remove an item from a booklist."""
        item_position = db.scalar(
            select(BookListItem.order_id)
            .where(BookListItem.booklist_id == booklist_orm_object.id)
            .where(BookListItem.work_id == item_to_remove.work_id)
        )
        if item_position is None:
            logger.warning("Got asked to remove an item that is already removed")
            return

        db.execute(
            delete(BookListItem)
            .where(BookListItem.booklist_id == booklist_orm_object.id)
            .where(BookListItem.work_id == item_to_remove.work_id)
        )
        db.commit()
        logger.debug(
            "Move all the following items up the list one", position=item_position
        )
        stmt = (
            update(BookListItem)
            .where(BookListItem.booklist_id == booklist_orm_object.id)
            .where(BookListItem.order_id > item_position)
            .values(order_id=BookListItem.order_id - 1)
        )
        db.execute(stmt)
        db.commit()
        db.refresh(booklist_orm_object)

    def _add_item_to_booklist(
        self,
        db: Session,
        booklist_orm_object: BookList,
        item_update: BookListItemUpdateIn,
    ):
        """Add an item to a booklist."""
        existing_item_position = db.scalar(
            select(BookListItem.order_id)
            .where(BookListItem.booklist_id == booklist_orm_object.id)
            .where(BookListItem.work_id == item_update.work_id)
        )
        if existing_item_position is not None:
            logger.debug("Got asked to add an item that is already present")
            return

        if item_update.order_id is None:
            new_order_id = booklist_orm_object.book_count
        else:
            stmt = (
                update(BookListItem)
                .where(BookListItem.booklist_id == booklist_orm_object.id)
                .where(BookListItem.order_id >= item_update.order_id)
                .values(order_id=BookListItem.order_id + 1)
            )
            db.execute(stmt)
            new_order_id = item_update.order_id

        new_orm_item = BookListItem(
            booklist_id=booklist_orm_object.id,
            work_id=item_update.work_id,
            info=item_update.info.model_dump()
            if item_update.info is not None
            else None,
            order_id=new_order_id,
        )

        db.add(new_orm_item)
        db.commit()
        db.refresh(booklist_orm_object)
        return new_orm_item

    async def _aadd_item_to_booklist(
        self,
        db: AsyncSession,
        booklist_orm_object: BookList,
        item_update: BookListItemUpdateIn,
    ):
        """Async version of _add_item_to_booklist."""
        existing_item_position = await db.scalar(
            select(BookListItem.order_id)
            .where(BookListItem.booklist_id == booklist_orm_object.id)
            .where(BookListItem.work_id == item_update.work_id)
        )
        if existing_item_position is not None:
            logger.debug("Got asked to add an item that is already present")
            return

        if item_update.order_id is None:
            new_order_id = booklist_orm_object.book_count
        else:
            stmt = (
                update(BookListItem)
                .where(BookListItem.booklist_id == booklist_orm_object.id)
                .where(BookListItem.order_id >= item_update.order_id)
                .values(order_id=BookListItem.order_id + 1)
            )
            await db.execute(stmt)
            new_order_id = item_update.order_id

        new_orm_item = BookListItem(
            booklist_id=booklist_orm_object.id,
            work_id=item_update.work_id,
            info=item_update.info.model_dump()
            if item_update.info is not None
            else None,
            order_id=new_order_id,
        )

        db.add(new_orm_item)
        await db.commit()
        await db.refresh(booklist_orm_object)
        return new_orm_item

    def remove(self, db: Session, id: int) -> BookList:
        """Delete a booklist by ID."""
        booklist = self.get_or_404(db, id)
        db.delete(booklist)
        db.commit()
        return booklist


# Singleton instance
booklist_repository = BooklistRepositoryImpl()

from typing import List
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    String,
    Table,
    and_,
    func,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, aliased
from sqlalchemy.sql.ddl import CreateTable
from starlette import status
from structlog import get_logger

from app import crud
from app.models import BookListItem, CollectionItem
from app.models.collection import Collection
from app.models.edition import Edition
from app.models.labelset import LabelSet, RecommendStatus
from app.models.work import Work
from app.repositories.edition_repository import edition_repository
from app.schemas.collection import (
    CollectionAndItemsUpdateIn,
    CollectionItemCreateIn,
    CollectionUpdateType,
)
from app.services.editions import get_definitive_isbn

logger = get_logger()


async def update_collection(
    session,
    collection: Collection,
    account,
    obj_in: CollectionAndItemsUpdateIn,
    merge_dicts: bool = True,
    ignore_conflicts: bool = False,
):
    item_changes = getattr(obj_in, "items", [])
    if item_changes is None:
        item_changes = []

    if len(item_changes) > 0:
        logger.info(
            f"Applying {len(item_changes)} changes",
            collection_id=str(collection.id),
            collection_name=collection.name,
        )
    # If provided, update the items one by one
    # First process in bulk editions added or updated by ISBN (could add support for bulk update by ID if needed)

    added_items = []
    updated_items = []
    for change in item_changes:
        if (
            change.action == CollectionUpdateType.ADD
            and change.edition_isbn is not None
        ):
            added_items.append(change)
        elif (
            change.action == CollectionUpdateType.UPDATE
            and change.edition_isbn is not None
        ):
            updated_items.append(change)

    if len(added_items) > 0:
        await add_editions_to_collection_by_isbn(
            session, added_items, collection, account
        )
        session.flush()
        logger.debug("Added editions", collection_id=str(collection.id))

    if len(updated_items) > 0:
        logger.info(
            "Updating existing editions in bulk", collection_id=str(collection.id)
        )
        await bulk_update_editions_in_collection_by_isbn(
            session, updated_items, collection, commit=False
        )
        session.flush()
        logger.debug("Updated editions", collection_id=str(collection.id))

    obj_in.items = [
        item
        for item in item_changes
        if CollectionUpdateType.REMOVE or item.edition_isbn is None
    ]
    logger.info(f"Update items now has {len(obj_in.items)} items")

    logger.debug(
        "Updating the collection object itself", collection_id=str(collection.id)
    )
    collection = crud.collection.update(
        db=session,
        db_obj=collection,
        obj_in=obj_in,
        merge_dicts=merge_dicts,
        ignore_conflicts=ignore_conflicts,
        commit=False,
    )
    logger.debug(
        "Committing update to collection",
        collection_id=str(collection.id),
        collection_name=collection.name,
    )
    session.commit()
    logger.debug(
        "Committed update to collection",
        collection_id=str(collection.id),
        collection_name=collection.name,
    )

    return collection


async def add_editions_to_collection_by_isbn(
    session,
    collection_data: List[CollectionItemCreateIn],
    collection: Collection,
    account,
):
    """
    Mostly the same as add_editions_to_collection, but only processes a list of isbns.
    Due to the lack of EditionCreateIns, any created editions will be unhydrated
    """
    logger.info(
        "Adding editions to collection by ISBN", account=account, collection=collection
    )

    items = {}
    for item in collection_data:
        try:
            item.edition_isbn = get_definitive_isbn(item.edition_isbn)
        except AssertionError:
            # Invalid isbn, just skip
            continue

        item.copies_total = item.copies_total or 1
        item.copies_available = item.copies_available or item.copies_total

        items[item.edition_isbn] = item

    isbn_list = list(items.keys())

    # Insert the entire list of isbns, ignoring conflicts, returning a list of the pk's for the CollectionItem binding
    (
        final_primary_keys,
        num_editions_created,
    ) = await edition_repository.create_in_bulk_unhydrated(session, isbn_list=isbn_list)

    if not final_primary_keys:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="No valid ISBNs were found in input",
        )

    # At this point all editions referenced should exist.
    # Using len(final_primary_keys) as length may be different now that it's a set
    logger.info(f"Syncing {len(final_primary_keys)} editions with collection")

    crud.collection.add_items_to_collection(
        session,
        collection_orm_object=collection,
        items=[items[isbn] for isbn in final_primary_keys],
        commit=False,
    )

    num_collection_items_created = len(final_primary_keys)

    crud.event.create(
        session=session,
        title="Collection Update",
        description=f"Adding {num_collection_items_created} editions to collection",
        info={
            "collection_items_created_count": num_collection_items_created,
            "unhydrated_edition_count": num_editions_created,
            "collection_id": str(collection.id),
        },
        account=account,
        commit=False,
    )
    logger.debug("Committing changes to collection")

    try:
        session.commit()
        logger.debug("Changes committed to collection")
    except IntegrityError:
        logger.warning(
            "IntegrityError when adding editions to collection", exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Couldn't process input",
        )


def _generate_temp_update_table_ddl(engine):
    metadata = MetaData()
    temp_table = Table(
        "temp_collection_update",
        metadata,
        Column("edition_isbn", String, primary_key=True),
        Column("copies_available", Integer),
        Column("copies_total", Integer),
        Column("info", JSONB, server_default=text("'{}'::jsonb"), nullable=False),
        prefixes=["TEMPORARY"],
    )

    # Using the SQLAlchemy engine to compile the SQL expression into a string
    return temp_table, str(CreateTable(temp_table).compile(bind=engine))


async def bulk_update_editions_in_collection_by_isbn(
    db: Session,
    items: List[CollectionItemCreateIn],
    collection_orm_object: Collection,
    commit: bool = False,
):
    temp_table, ddl = _generate_temp_update_table_ddl(db.bind)
    logger.debug("Creating temporary table", ddl=ddl)
    db.execute(text(ddl))
    logger.debug("Created temporary table for bulk update")
    try:
        # Insert raw data into the temporary table
        db.execute(
            temp_table.insert(),
            [item.model_dump(mode="json", exclude_unset=True) for item in items],
        )

        logger.debug("Perform the update to collection_items from the temporary table")
        update_stmt = (
            update(CollectionItem)
            .where(CollectionItem.edition_isbn == temp_table.c.edition_isbn)
            .where(CollectionItem.collection_id == collection_orm_object.id)
            .values(
                copies_available=temp_table.c.copies_available,
                copies_total=temp_table.c.copies_total,
                info=CollectionItem.info.concat(temp_table.c.info),
            )
        )
        db.execute(update_stmt)
        db.execute(text("drop table if exists temp_collection_update"))

        if commit:
            db.commit()

    except Exception as e:
        logger.error(f"Error during bulk update: {str(e)}")
        db.rollback()
        raise

    logger.info("Bulk update via temporary table completed successfully.")


async def get_collection_info_with_criteria(
    session: AsyncSession,
    collection_id: UUID,
    # is_hydrated: bool = False,
    # is_labelled: bool = False,
    # is_recommendable: bool = False,
    # cte_label: str = "latestlabelset",
):
    """
    Return a (complicated) select query for labelsets, editions, and works filtering by
    collection, hydration status, labelling status, and recommendability.

    Can raise sqlalchemy.exc.NoResultFound if for example an invalid reading_ability key
    is passed.
    """

    query = (
        select(
            func.count().label("total"),
            func.count()
            .filter(and_(Edition.title.is_not(None), Edition.cover_url.is_not(None)))
            .label("hydrated"),
            func.count()
            .filter(
                and_(
                    Edition.title.is_not(None),
                    Edition.cover_url.is_not(None),
                    LabelSet.hues.any(),
                    LabelSet.reading_abilities.any(),
                    LabelSet.min_age >= 0,
                    LabelSet.max_age > 0,
                    LabelSet.huey_summary.is_not(None),
                )
            )
            .label("labelled"),
            func.count()
            .filter(
                and_(
                    Edition.title.is_not(None),
                    Edition.cover_url.is_not(None),
                    LabelSet.hues.any(),
                    LabelSet.reading_abilities.any(),
                    LabelSet.min_age >= 0,
                    LabelSet.max_age > 0,
                    LabelSet.huey_summary.is_not(None),
                    LabelSet.recommend_status == RecommendStatus.GOOD,
                )
            )
            .label("recommendable"),
        )
        .select_from(Work)
        .join(Edition, Edition.work_id == Work.id)
        .join(CollectionItem, CollectionItem.edition_isbn == Edition.isbn)
        .join(LabelSet, LabelSet.work_id == Work.id, isouter=True)
        .where(CollectionItem.collection_id == collection_id)
    )

    # if config.DEBUG:
    #     explain_results = (await session.execute(explain(query, analyze=True))).scalars().all()
    #     logger.info("Query plan")
    #     for entry in explain_results:
    #         logger.info(entry)

    result = (await session.execute(query)).fetchone()
    return {
        "total_editions": result.total,
        "hydrated": result.hydrated,
        "hydrated_and_labeled": result.labelled,
        "recommendable": result.recommendable,
    }


async def get_collection_items_also_in_booklist(
    session, collection, paginated_booklist_item_query
) -> list[CollectionItem]:
    paginated_booklist_cte = paginated_booklist_item_query.cte(
        name="paginated_booklist_items"
    )

    aliased_booklist_item = aliased(BookListItem, paginated_booklist_cte)
    booklist_item_work_id_query = select(aliased_booklist_item.work_id)
    common_collection_items = session.scalars(
        collection.items.statement.where(
            CollectionItem.work_id.in_(booklist_item_work_id_query)
        )
    ).all()

    return common_collection_items


def reset_collection(session, collection: Collection, account):
    """
    Reset a collection to its initial state, removing all items
    """
    crud.collection.delete_all_items(db=session, db_obj=collection, commit=False)
    collection.updated_at = text("default")

    crud.event.create(
        session=session,
        title="Collection Reset",
        description=f"Reset collection #{str(collection.id)}, deleting all items",
        info={"collection_id": str(collection.id)},
        account=account,
        commit=True,
    )

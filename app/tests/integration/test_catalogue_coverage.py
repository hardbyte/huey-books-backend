from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import event, select, text

from app.api.common.pagination import PaginatedQueryParams
from app.models import Author, Collection, CollectionItem, Edition, Work
from app.models.collection_item_activity import (
    CollectionItemActivity,
    CollectionItemReadStatus,
)
from app.models.labelset import LabelSet
from app.models.public_reader import PublicReader
from app.repositories.collection_repository import collection_repository
from app.schemas.collection import CollectionItemDetail
from app.services.collections import get_collection_info_with_criteria
from app.services.search import book_search


async def test_search_includes_missing_metadata_and_combines_series(async_session):
    work_ids = []
    for _ in range(3):
        work_ids.append(
            await async_session.scalar(
                text(
                    "INSERT INTO works(title, type) VALUES ('Coverageprobex', 'BOOK') RETURNING id"
                )
            )
        )
    author_id = await async_session.scalar(
        text(
            "INSERT INTO authors(first_name, last_name) VALUES (NULL, 'Onlysurnameprobe') RETURNING id"
        )
    )
    await async_session.execute(
        text(
            "INSERT INTO author_work_association(work_id, author_id) VALUES (:work, :author)"
        ),
        {"work": work_ids[1], "author": author_id},
    )
    for title in ["Seriesalphaprobe", "Seriesbetaprobe"]:
        series_id = await async_session.scalar(
            text("INSERT INTO series(title) VALUES (:title) RETURNING id"),
            {"title": title},
        )
        await async_session.execute(
            text(
                "INSERT INTO series_works_association(work_id, series_id) VALUES (:work, :series)"
            ),
            {"work": work_ids[2], "series": series_id},
        )
    await async_session.execute(text("SELECT public.refresh_search_index()"))
    rows = (
        (
            await async_session.execute(
                text(
                    "SELECT work_id FROM search_view_v1 WHERE work_id = ANY(:ids) ORDER BY work_id"
                ),
                {"ids": work_ids},
            )
        )
        .scalars()
        .all()
    )
    assert rows == work_ids
    results = await book_search(
        async_session, PaginatedQueryParams(skip=0, limit=10), "Coverageprobex"
    )
    assert [int(row.id) for row in results] == work_ids
    assert results[0].authors == []
    for query, expected in [
        ("Onlysurnameprobe", work_ids[1]),
        ("Seriesalphaprobe", work_ids[2]),
        ("Seriesbetaprobe", work_ids[2]),
    ]:
        results = await book_search(
            async_session, PaginatedQueryParams(skip=0, limit=10), query
        )
        assert [int(row.id) for row in results] == [expected]
    for offset, expected in enumerate(work_ids):
        results = await book_search(
            async_session, PaginatedQueryParams(skip=offset, limit=1), "Coverageprobex"
        )
        assert [int(row.id) for row in results] == [expected]
    filtered = await book_search(
        async_session,
        PaginatedQueryParams(skip=0, limit=10),
        "Coverageprobex",
        author_id,
    )
    assert [int(row.id) for row in filtered] == [work_ids[1]]

    await async_session.execute(
        text("DELETE FROM works WHERE id = :id"), {"id": work_ids[0]}
    )
    remaining = await book_search(
        async_session, PaginatedQueryParams(skip=0, limit=2), "Coverageprobex"
    )
    assert [int(row.id) for row in remaining] == work_ids[1:]


async def test_collection_info_counts_holdings_not_label_history(async_session):
    collection = Collection(name="Scoped count fixture")
    work = Work(title="Scoped count fixture")
    edition = Edition(
        isbn=f"count-{uuid4()}",
        title=work.title,
        work=work,
        cover_url="https://example.com/cover.jpg",
    )
    unresolved = Edition(isbn=f"unresolved-{uuid4()}", title="Unresolved")
    async_session.add_all([collection, edition, unresolved])
    await async_session.flush()
    async_session.add_all(
        [
            CollectionItem(
                collection_id=collection.id, edition_isbn=edition.isbn, copies_total=7
            ),
            CollectionItem(collection_id=collection.id, edition_isbn=unresolved.isbn),
            CollectionItem(collection_id=collection.id),
            LabelSet(work_id=work.id),
            LabelSet(work_id=work.id),
        ]
    )
    await async_session.flush()
    result = await get_collection_info_with_criteria(async_session, collection.id)
    assert result == {
        "total_editions": 3,
        "hydrated": 1,
        "hydrated_and_labeled": 0,
        "recommendable": 0,
    }
    latest = await async_session.scalar(
        select(LabelSet)
        .where(LabelSet.work_id == work.id)
        .order_by(LabelSet.id.desc())
        .limit(1)
    )
    hue_id = await async_session.scalar(text("SELECT id FROM hues LIMIT 1"))
    ability_id = await async_session.scalar(
        text("SELECT id FROM reading_abilities LIMIT 1")
    )
    assert hue_id is not None and ability_id is not None
    await async_session.execute(
        text(
            "INSERT INTO labelset_hue_association(labelset_id,hue_id,ordinal) VALUES (:label,:hue,'PRIMARY')"
        ),
        {"label": latest.id, "hue": hue_id},
    )
    await async_session.execute(
        text(
            "INSERT INTO labelset_reading_ability_association(labelset_id,reading_ability_id) VALUES (:label,:ability)"
        ),
        {"label": latest.id, "ability": ability_id},
    )
    latest.min_age, latest.max_age, latest.huey_summary = 0, 12, "Synthetic summary"
    await async_session.flush()
    result = await get_collection_info_with_criteria(async_session, collection.id)
    assert result["hydrated_and_labeled"] == result["recommendable"] == 1
    async_session.add(LabelSet(work_id=work.id))
    await async_session.flush()
    result = await get_collection_info_with_criteria(async_session, collection.id)
    assert result["total_editions"] == 3
    assert result["hydrated_and_labeled"] == result["recommendable"] == 0
    assert await get_collection_info_with_criteria(async_session, uuid4()) == {
        "total_editions": 0,
        "hydrated": 0,
        "hydrated_and_labeled": 0,
        "recommendable": 0,
    }


def test_holdings_page_uses_latest_per_item_reader_without_duplicates(session):
    collection = Collection(name="Holdings pagination fixture")
    author = Author(first_name="Holding", last_name="Fixture")
    work = Work(title="Identical title", authors=[author], info={"large": "x" * 10000})
    reader = PublicReader(name=f"reader-{uuid4()}")
    other_reader = PublicReader(name=f"reader-{uuid4()}")
    session.add_all([collection, work, reader, other_reader])
    session.flush()
    items = []
    for _ in range(3):
        edition = Edition(
            isbn=f"holding-{uuid4()}",
            title=work.title,
            work=work,
            info={"large": "x" * 10000},
        )
        item = CollectionItem(collection_id=collection.id, edition=edition)
        session.add(item)
        items.append(item)
    session.flush()
    timestamp = datetime(2020, 1, 1)
    for item, status, when, actor in [
        (items[0], "READ", timestamp, reader),
        (items[0], "READING", timestamp, reader),
        (items[1], "READ", timestamp, reader),
        (items[1], "READ", timestamp + timedelta(seconds=1), reader),
        (items[1], "READ", timestamp, other_reader),
        (items[2], "READING", timestamp + timedelta(seconds=1), reader),
    ]:
        session.add(
            CollectionItemActivity(
                collection_item_id=item.id,
                reader_id=actor.id,
                status=CollectionItemReadStatus(status),
                timestamp=when,
            )
        )
        session.flush()
    collection_id, reader_id = collection.id, reader.id
    item_ids = [item.id for item in items]
    session.expunge_all()
    statements = []

    def collect(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(session.bind, "before_cursor_execute", collect)
    try:
        count, page = collection_repository.get_filtered_with_count(
            session, collection_id, skip=1, limit=1
        )
        assert count == 3 and [item.id for item in page] == [item_ids[1]]
        before = len(statements)
        CollectionItemDetail.model_validate(page[0])
        assert len(statements) == before == 3
        assert "editions.info" not in " ".join(statements)
        assert "works.info" not in " ".join(statements)
        for actor in [reader_id, None]:
            count, page = collection_repository.get_filtered_with_count(
                session,
                collection_id,
                reader_id=actor,
                read_status=CollectionItemReadStatus.READ,
            )
            assert count == 1 and [item.id for item in page] == [item_ids[1]]
        count, page = collection_repository.get_filtered_with_count(
            session, collection_id, reader_id=reader_id
        )
        assert count == 3 and [item.id for item in page] == item_ids
        assert (
            session.scalars(
                select(Edition.title)
                .join(CollectionItem, CollectionItem.edition_isbn == Edition.isbn)
                .where(CollectionItem.collection_id == collection_id)
            ).all()
            == ["Identical title"] * 3
        )
        count, page = collection_repository.get_filtered_with_count(
            session, collection_id, query_string="Identical", skip=1, limit=1
        )
        assert count == 3 and [item.id for item in page] == [item_ids[1]]
        count, page = collection_repository.get_filtered_with_count(
            session, collection_id, query_string="Unmatchedprobex"
        )
        assert count == 0 and page == []
    finally:
        event.remove(session.bind, "before_cursor_execute", collect)


def test_edition_titles_follow_the_associated_work(session):
    first = Work(title="First work fixture")
    second = Work(title="Second work fixture")
    unrelated = Work(title="Unrelated work fixture")
    session.add_all([first, second, unrelated])
    session.flush()
    edition = Edition(isbn=f"title-{uuid4()}", work=first)
    overridden = Edition(
        isbn=f"title-{uuid4()}", work=first, edition_title="Local title"
    )
    standalone = Edition(isbn=f"title-{uuid4()}", edition_title="Standalone title")
    session.add_all([edition, overridden, standalone])
    session.flush()

    def title(item):
        return session.scalar(select(Edition.title).where(Edition.id == item.id))

    assert title(edition) == first.title
    assert title(overridden) == "Local title"
    assert title(standalone) == "Standalone title"
    first.title = "Renamed first work"
    session.flush()
    assert title(edition) == first.title
    assert title(overridden) == "Local title"
    edition.work = second
    session.flush()
    assert title(edition) == second.title
    edition.work = None
    standalone.edition_title = None
    session.flush()
    assert title(edition) is None
    assert title(standalone) is None


def test_title_migration_repairs_only_derived_values(session):
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    work = Work(title="Canonical fixture title")
    edition = Edition(isbn=f"repair-{uuid4()}", work=work)
    override = Edition(
        isbn=f"repair-{uuid4()}", work=work, edition_title="Local fixture title"
    )
    unlinked = Edition(isbn=f"repair-{uuid4()}", edition_title="Unlinked fixture title")
    session.add_all([edition, override, unlinked])
    session.flush()
    ids = [edition.id, override.id, unlinked.id]
    session.execute(
        text("UPDATE editions SET title='Incorrect derived title' WHERE id=ANY(:ids)"),
        {"ids": ids},
    )
    path = (
        Path(__file__).resolve().parents[3]
        / "alembic/versions/f461cb85cd23_correct_edition_title_triggers.py"
    )
    spec = importlib.util.spec_from_file_location("title_revision", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with Operations.context(MigrationContext.configure(session.connection())):
        migration.upgrade()
    rows = session.execute(
        select(Edition.title, Edition.edition_title)
        .where(Edition.id.in_(ids))
        .order_by(Edition.id)
    ).all()
    assert rows == [
        (work.title, None),
        ("Local fixture title", "Local fixture title"),
        ("Unlinked fixture title", "Unlinked fixture title"),
    ]

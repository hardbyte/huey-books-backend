import pytest
from sqlalchemy import delete, event, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.api.common.pagination import PaginatedQueryParams
from app.api.editions import get_editions
from app.models import Author, Edition, SearchIndexRefresh, Series, Work
from app.schemas.edition import EditionBrief
from app.services.search import update_search_view_v1


async def test_search_refresh_records_both_indexes(async_session: AsyncSession):
    await update_search_view_v1(async_session)
    async with AsyncSession(async_session.bind) as observer:
        rows = (
            await observer.scalars(
                select(SearchIndexRefresh).where(
                    SearchIndexRefresh.index_name.in_(
                        ["search_view_v1", "work_collection_frequency"]
                    )
                )
            )
        ).all()
        assert len(rows) == 2
        assert all(row.source_snapshot_at <= row.refreshed_at for row in rows)


async def test_recommendation_refresh_timestamp_rolls_back(async_session: AsyncSession):
    before = await async_session.scalar(
        select(SearchIndexRefresh.source_snapshot_at).where(
            SearchIndexRefresh.index_name == "recommendable_editions"
        )
    )
    await async_session.execute(
        text("SELECT public.refresh_recommendable_editions_function()")
    )
    during = await async_session.scalar(
        select(SearchIndexRefresh.source_snapshot_at).where(
            SearchIndexRefresh.index_name == "recommendable_editions"
        )
    )
    assert during is not None and (before is None or during > before)
    await async_session.rollback()
    after = await async_session.scalar(
        select(SearchIndexRefresh.source_snapshot_at).where(
            SearchIndexRefresh.index_name == "recommendable_editions"
        )
    )
    assert after == before


@pytest.mark.parametrize("mode", ["all", "query", "work"])
def test_edition_list_paginates_without_relationship_loads(session: Session, mode):
    work = Work(title="Paginationprobe") if mode == "work" else None
    editions = [
        Edition(
            isbn=f"freshness-test-{n}",
            edition_title="Paginationprobe",
            title="Paginationprobe",
            work=work,
        )
        for n in range(4)
    ]
    session.add_all(editions)
    session.commit()
    edition_isbns = [edition.isbn for edition in editions]
    work_id = str(work.id) if work else None
    session.expunge_all()
    statements = []

    def count_queries(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(session.bind, "before_cursor_execute", count_queries)
    try:
        result = get_editions(
            work_id=work_id,
            query="Paginationprobe" if mode == "query" else None,
            pagination=PaginatedQueryParams(skip=1, limit=2),
            session=session,
        )
        serialized = [EditionBrief.model_validate(row) for row in result]
        assert len(result) == 2
        assert len(statements) == (2 if mode == "work" else 1)
        if mode != "all":
            assert [row.isbn for row in serialized] == edition_isbns[1:3]
    finally:
        event.remove(session.bind, "before_cursor_execute", count_queries)
        session.execute(delete(Edition).where(Edition.isbn.in_(edition_isbns)))
        if work_id:
            session.execute(delete(Work).where(Work.id == int(work_id)))
        session.commit()


async def test_blocked_refresh_times_out_without_advancing_freshness(
    async_session: AsyncSession, session: Session
):
    await update_search_view_v1(async_session)
    before = await async_session.scalar(
        select(SearchIndexRefresh.source_snapshot_at).where(
            SearchIndexRefresh.index_name == "search_view_v1"
        )
    )
    # Retain a conflicting maintenance lock in a separate transaction.
    with session.bind.connect() as blocker:
        blocker.execute(text("REFRESH MATERIALIZED VIEW public.search_view_v1"))
        with pytest.raises(DBAPIError, match="lock timeout"):
            await update_search_view_v1(async_session)
        await async_session.rollback()
    after = await async_session.scalar(
        select(SearchIndexRefresh.source_snapshot_at).where(
            SearchIndexRefresh.index_name == "search_view_v1"
        )
    )
    assert after == before


async def test_concurrent_search_refresh_preserves_null_and_multiple_series(
    async_session: AsyncSession, session: Session
):
    author = Author(first_name="Freshness", last_name="Fixture")
    series = [Series(title=f"Freshness Fixture Series {n}") for n in range(2)]
    standalone = Work(title="Freshness standalone", authors=[author])
    multiseries = Work(
        title="Freshness multiple series", authors=[author], series=series
    )
    session.add_all([standalone, multiseries])
    session.commit()
    standalone_id, multi_id = standalone.id, multiseries.id
    series_ids, author_id = [item.id for item in series], author.id
    session.rollback()
    rows_sql = text(
        "SELECT work_id, series_id, document::text FROM search_view_v1 WHERE work_id IN (:standalone, :multi) ORDER BY work_id, series_id"
    )
    parameters = {"standalone": standalone_id, "multi": multi_id}
    try:
        await update_search_view_v1(async_session)
        before = (await async_session.execute(rows_sql, parameters)).all()
        assert len(before) == 2
        assert [(row.work_id, row.series_id) for row in before] == [
            (standalone_id, None),
            (multi_id, None),
        ]
        with session.bind.connect() as reader:
            reader.execute(text("SELECT 1 FROM search_view_v1 LIMIT 1"))
            await update_search_view_v1(async_session)
        assert (await async_session.execute(rows_sql, parameters)).all() == before
        session.execute(
            text("UPDATE works SET title = 'Changed freshness title' WHERE id = :id"),
            {"id": standalone_id},
        )
        session.commit()
        await update_search_view_v1(async_session)
        after = (await async_session.execute(rows_sql, parameters)).all()
        assert len(after) == 2
        assert after[0].document != before[0].document
    finally:
        await async_session.rollback()
        session.execute(
            text(
                "DELETE FROM series_works_association WHERE work_id IN (:standalone, :multi)"
            ),
            parameters,
        )
        session.execute(
            text(
                "DELETE FROM author_work_association WHERE work_id IN (:standalone, :multi)"
            ),
            parameters,
        )
        session.execute(delete(Work).where(Work.id.in_([standalone_id, multi_id])))
        session.execute(delete(Series).where(Series.id.in_(series_ids)))
        session.execute(delete(Author).where(Author.id == author_id))
        session.commit()
        await update_search_view_v1(async_session)

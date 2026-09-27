"""
Integration tests for PostgreSQL materialized views.

This module tests the search_view_v1 materialized view that provides full-text search
functionality. Tests verify that data updates to source tables appear in the
materialized view after manual refresh and that search functionality works correctly.
"""

import logging
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.work import WorkType
from app.repositories.work_repository import work_repository
from app.schemas.author import AuthorCreateIn
from app.schemas.work import WorkCreateIn

# Series operations removed for simplicity

logger = logging.getLogger(__name__)


@pytest.fixture
async def cleanup_test_data(async_session: AsyncSession):
    """Cleanup fixture to remove test data after each test."""
    test_titles = []
    test_author_names = []
    test_series_names = []  # Keep for backward compatibility but won't use

    yield test_titles, test_author_names, test_series_names

    # Cleanup works by title
    for title in test_titles:
        try:
            result = await async_session.execute(
                text("SELECT id FROM works WHERE title ILIKE :title"),
                {"title": f"%{title}%"},
            )
            work_ids = [row[0] for row in result.fetchall()]

            for work_id in work_ids:
                # Delete associated data first
                await async_session.execute(
                    text(
                        "DELETE FROM author_work_association WHERE work_id = :work_id"
                    ),
                    {"work_id": work_id},
                )
                await async_session.execute(
                    text(
                        "DELETE FROM series_works_association WHERE work_id = :work_id"
                    ),
                    {"work_id": work_id},
                )
                await async_session.execute(
                    text("DELETE FROM editions WHERE work_id = :work_id"),
                    {"work_id": work_id},
                )
                await async_session.execute(
                    text("DELETE FROM works WHERE id = :work_id"), {"work_id": work_id}
                )
            await async_session.commit()
        except Exception as e:
            logger.warning(f"Cleanup error for title {title}: {e}")
            await async_session.rollback()

    # Cleanup authors by name
    for author_name in test_author_names:
        try:
            first_name, last_name = author_name.split(" ", 1)
            result = await async_session.execute(
                text(
                    "SELECT id FROM authors WHERE first_name = :first_name AND last_name = :last_name"
                ),
                {"first_name": first_name, "last_name": last_name},
            )
            author_ids = [row[0] for row in result.fetchall()]

            for author_id in author_ids:
                await async_session.execute(
                    text(
                        "DELETE FROM author_work_association WHERE author_id = :author_id"
                    ),
                    {"author_id": author_id},
                )
                await async_session.execute(
                    text("DELETE FROM authors WHERE id = :author_id"),
                    {"author_id": author_id},
                )
            await async_session.commit()
        except Exception as e:
            logger.warning(f"Cleanup error for author {author_name}: {e}")
            await async_session.rollback()

    # Cleanup series by title
    for series_title in test_series_names:
        try:
            result = await async_session.execute(
                text("SELECT id FROM series WHERE title = :title"),
                {"title": series_title},
            )
            series_ids = [row[0] for row in result.fetchall()]

            for series_id in series_ids:
                await async_session.execute(
                    text(
                        "DELETE FROM series_works_association WHERE series_id = :series_id"
                    ),
                    {"series_id": series_id},
                )
                await async_session.execute(
                    text("DELETE FROM series WHERE id = :series_id"),
                    {"series_id": series_id},
                )
            await async_session.commit()
        except Exception as e:
            logger.warning(f"Cleanup error for series {series_title}: {e}")
            await async_session.rollback()


class TestSearchViewV1MaterializedView:
    """Test cases for the search_view_v1 materialized view."""

    async def test_search_view_refresh_after_work_creation(
        self, async_session: AsyncSession, cleanup_test_data
    ):
        """Test that search_view_v1 reflects new work data after refresh."""
        test_titles, test_author_names, test_series_names = cleanup_test_data

        # Create unique test data
        test_title = f"Database Test Book {uuid.uuid4().hex[:8]}"
        author_name = f"Test Author {uuid.uuid4().hex[:6]}"
        first_name, last_name = author_name.split(" ", 1)

        test_titles.append(test_title)
        test_author_names.append(author_name)

        # Check that the work doesn't exist in search view initially
        initial_result = await async_session.execute(
            text(
                "SELECT COUNT(*) FROM search_view_v1 WHERE work_id IN (SELECT id FROM works WHERE title = :title)"
            ),
            {"title": test_title},
        )
        initial_count = initial_result.scalar()
        assert initial_count == 0

        # Add new work to source table
        new_work = await work_repository.acreate(
            db=async_session,
            obj_in=WorkCreateIn(
                title=test_title,
                type=WorkType.BOOK,
                authors=[AuthorCreateIn(first_name=first_name, last_name=last_name)],
            ),
        )

        # Verify work was created but not yet in materialized view
        pre_refresh_result = await async_session.execute(
            text("SELECT COUNT(*) FROM search_view_v1 WHERE work_id = :work_id"),
            {"work_id": new_work.id},
        )
        pre_refresh_count = pre_refresh_result.scalar()
        assert pre_refresh_count == 0, (
            "Work should not be in materialized view before refresh"
        )

        # Manually refresh the materialized view
        await async_session.execute(text("REFRESH MATERIALIZED VIEW search_view_v1"))
        await async_session.commit()

        # Query the materialized view to verify new data appears
        post_refresh_result = await async_session.execute(
            text(
                "SELECT work_id, author_ids FROM search_view_v1 WHERE work_id = :work_id"
            ),
            {"work_id": new_work.id},
        )

        rows = post_refresh_result.fetchall()
        assert len(rows) == 1, f"Expected 1 row in search view, got {len(rows)}"

        row = rows[0]
        assert row[0] == new_work.id
        assert isinstance(row[1], list), "Author IDs should be a JSON array"
        assert len(row[1]) > 0, "Should have at least one author ID"

    async def test_search_view_full_text_search_functionality(
        self, async_session: AsyncSession, cleanup_test_data
    ):
        """Test that full-text search works correctly with the materialized view."""
        test_titles, test_author_names, test_series_names = cleanup_test_data

        # Create test data with searchable content
        test_title = f"Quantum Physics Adventures {uuid.uuid4().hex[:6]}"
        author_name = f"Marie Scientist {uuid.uuid4().hex[:6]}"
        first_name, last_name = author_name.split(" ", 1)

        test_titles.append(test_title)
        test_author_names.append(author_name)

        # Create work with searchable content
        new_work = await work_repository.acreate(
            db=async_session,
            obj_in=WorkCreateIn(
                title=test_title,
                subtitle="An Exploration of Modern Physics",
                type=WorkType.BOOK,
                authors=[AuthorCreateIn(first_name=first_name, last_name=last_name)],
            ),
        )

        # Refresh materialized view
        await async_session.execute(text("REFRESH MATERIALIZED VIEW search_view_v1"))
        await async_session.commit()

        # Test search by title
        title_search_result = await async_session.execute(
            text("""
                SELECT work_id, ts_rank(document, plainto_tsquery('english', :query)) as rank
                FROM search_view_v1 
                WHERE document @@ plainto_tsquery('english', :query)
                AND work_id = :work_id
            """),
            {"query": "Quantum Physics", "work_id": new_work.id},
        )

        title_rows = title_search_result.fetchall()
        assert len(title_rows) == 1, "Should find work by title search"
        assert title_rows[0][1] > 0, "Should have positive search rank"

        # Test search by author name
        author_search_result = await async_session.execute(
            text("""
                SELECT work_id, ts_rank(document, plainto_tsquery('english', :query)) as rank
                FROM search_view_v1 
                WHERE document @@ plainto_tsquery('english', :query)
                AND work_id = :work_id
            """),
            {"query": "Marie Scientist", "work_id": new_work.id},
        )

        author_rows = author_search_result.fetchall()
        assert len(author_rows) == 1, "Should find work by author search"
        assert author_rows[0][1] > 0, "Should have positive search rank"

        # Test search by subtitle
        subtitle_search_result = await async_session.execute(
            text("""
                SELECT work_id, ts_rank(document, plainto_tsquery('english', :query)) as rank
                FROM search_view_v1 
                WHERE document @@ plainto_tsquery('english', :query)
                AND work_id = :work_id
            """),
            {"query": "Exploration Modern", "work_id": new_work.id},
        )

        subtitle_rows = subtitle_search_result.fetchall()
        assert len(subtitle_rows) == 1, "Should find work by subtitle search"

    async def test_search_view_basic_structure(
        self, async_session: AsyncSession, cleanup_test_data
    ):
        """Test that search view has the expected structure and columns."""
        test_titles, test_author_names, test_series_names = cleanup_test_data

        # Create test data
        work_title = f"Structure Test Book {uuid.uuid4().hex[:6]}"
        author_name = f"Structure Author {uuid.uuid4().hex[:6]}"
        first_name, last_name = author_name.split(" ", 1)

        test_titles.append(work_title)
        test_author_names.append(author_name)

        # Create work
        work = await work_repository.acreate(
            db=async_session,
            obj_in=WorkCreateIn(
                title=work_title,
                type=WorkType.BOOK,
                authors=[AuthorCreateIn(first_name=first_name, last_name=last_name)],
            ),
        )

        # Refresh materialized view
        await async_session.execute(text("REFRESH MATERIALIZED VIEW search_view_v1"))
        await async_session.commit()

        # Check the view structure
        view_result = await async_session.execute(
            text(
                "SELECT work_id, author_ids, series_id FROM search_view_v1 WHERE work_id = :work_id"
            ),
            {"work_id": work.id},
        )

        rows = view_result.fetchall()
        assert len(rows) == 1

        work_id, author_ids, series_id = rows[0]
        assert work_id == work.id
        assert isinstance(author_ids, list), "Author IDs should be a JSON array"
        assert len(author_ids) > 0, "Should have at least one author ID"
        # series_id can be None for works without series

    async def test_search_view_staleness_without_refresh(
        self, async_session: AsyncSession, cleanup_test_data
    ):
        """Test that without refresh, new data doesn't appear in search view."""
        test_titles, test_author_names, test_series_names = cleanup_test_data

        # Create a unique work for this test
        test_title = f"Stale Test Book {uuid.uuid4().hex[:8]}"
        author_name = f"Stale Author {uuid.uuid4().hex[:6]}"
        first_name, last_name = author_name.split(" ", 1)

        test_titles.append(test_title)
        test_author_names.append(author_name)

        new_work = await work_repository.acreate(
            db=async_session,
            obj_in=WorkCreateIn(
                title=test_title,
                type=WorkType.BOOK,
                authors=[AuthorCreateIn(first_name=first_name, last_name=last_name)],
            ),
        )

        # Verify the specific work is not in the view before refresh
        work_search_result = await async_session.execute(
            text("SELECT COUNT(*) FROM search_view_v1 WHERE work_id = :work_id"),
            {"work_id": new_work.id},
        )
        work_count = work_search_result.scalar()
        assert work_count == 0, (
            "New work should not be in the materialized view before refresh"
        )

        # Now refresh and verify it appears
        await async_session.execute(text("REFRESH MATERIALIZED VIEW search_view_v1"))
        await async_session.commit()

        # Verify the specific work is now in the view
        refreshed_work_result = await async_session.execute(
            text("SELECT COUNT(*) FROM search_view_v1 WHERE work_id = :work_id"),
            {"work_id": new_work.id},
        )
        refreshed_work_count = refreshed_work_result.scalar()
        assert refreshed_work_count == 1, (
            "New work should be in the refreshed materialized view"
        )

    async def test_search_view_document_weights(
        self, async_session: AsyncSession, cleanup_test_data
    ):
        """Test that search view applies correct text weights (title > subtitle > author > series)."""
        test_titles, test_author_names, test_series_names = cleanup_test_data

        # Create test data with same search term in different fields
        search_term = f"relevance{uuid.uuid4().hex[:6]}"

        # Work 1: Search term in title (highest weight 'A')
        title1 = f"{search_term} in Title"
        work1 = await work_repository.acreate(
            db=async_session,
            obj_in=WorkCreateIn(
                title=title1,
                subtitle="Different subtitle",
                type=WorkType.BOOK,
                authors=[AuthorCreateIn(first_name="Different", last_name="Author")],
            ),
        )

        # Work 2: Search term in subtitle (weight 'C')
        title2 = f"Different Title {uuid.uuid4().hex[:6]}"
        work2 = await work_repository.acreate(
            db=async_session,
            obj_in=WorkCreateIn(
                title=title2,
                subtitle=f"{search_term} in Subtitle",
                type=WorkType.BOOK,
                authors=[AuthorCreateIn(first_name="Different", last_name="Author")],
            ),
        )

        # Work 3: Search term in author name (weight 'C')
        title3 = f"Another Title {uuid.uuid4().hex[:6]}"
        work3 = await work_repository.acreate(
            db=async_session,
            obj_in=WorkCreateIn(
                title=title3,
                subtitle="Different subtitle",
                type=WorkType.BOOK,
                authors=[AuthorCreateIn(first_name=search_term, last_name="Author")],
            ),
        )

        test_titles.extend([title1, title2, title3])
        test_author_names.extend(["Different Author", f"{search_term} Author"])

        # Refresh materialized view
        await async_session.execute(text("REFRESH MATERIALIZED VIEW search_view_v1"))
        await async_session.commit()

        # Search and get rankings
        ranking_result = await async_session.execute(
            text("""
                SELECT work_id, ts_rank(document, plainto_tsquery('english', :query)) as rank
                FROM search_view_v1 
                WHERE document @@ plainto_tsquery('english', :query)
                AND work_id IN (:work1_id, :work2_id, :work3_id)
                ORDER BY rank DESC
            """),
            {
                "query": search_term,
                "work1_id": work1.id,
                "work2_id": work2.id,
                "work3_id": work3.id,
            },
        )

        ranked_results = ranking_result.fetchall()
        assert len(ranked_results) == 3, "Should find all three works"

        # Verify ranking order: title match should rank highest
        work_ids_by_rank = [row[0] for row in ranked_results]
        ranks = [row[1] for row in ranked_results]

        # Title match should have highest rank
        assert work_ids_by_rank[0] == work1.id, (
            "Work with search term in title should rank highest"
        )

        # All ranks should be positive
        for rank in ranks:
            assert rank > 0, "All matching works should have positive rank"

        # Title match should have higher rank than subtitle/author matches
        title_rank = ranks[0]
        other_ranks = ranks[1:]
        for other_rank in other_ranks:
            assert title_rank > other_rank, (
                "Title match should rank higher than subtitle/author matches"
            )

    async def test_search_view_with_multiple_authors(
        self, async_session: AsyncSession, cleanup_test_data
    ):
        """Test that search view handles works with multiple authors correctly."""
        test_titles, test_author_names, test_series_names = cleanup_test_data

        # Create work with multiple authors
        test_title = f"Multi Author Book {uuid.uuid4().hex[:8]}"
        author1_name = f"First Author {uuid.uuid4().hex[:6]}"
        author2_name = f"Second Author {uuid.uuid4().hex[:6]}"

        first1, last1 = author1_name.split(" ", 1)
        first2, last2 = author2_name.split(" ", 1)

        test_titles.append(test_title)
        test_author_names.extend([author1_name, author2_name])

        multi_author_work = await work_repository.acreate(
            db=async_session,
            obj_in=WorkCreateIn(
                title=test_title,
                type=WorkType.BOOK,
                authors=[
                    AuthorCreateIn(first_name=first1, last_name=last1),
                    AuthorCreateIn(first_name=first2, last_name=last2),
                ],
            ),
        )

        # Refresh materialized view
        await async_session.execute(text("REFRESH MATERIALIZED VIEW search_view_v1"))
        await async_session.commit()

        # Query the view
        multi_author_result = await async_session.execute(
            text(
                "SELECT work_id, author_ids FROM search_view_v1 WHERE work_id = :work_id"
            ),
            {"work_id": multi_author_work.id},
        )

        rows = multi_author_result.fetchall()
        assert len(rows) == 1

        work_id, author_ids = rows[0]
        assert work_id == multi_author_work.id
        assert isinstance(author_ids, list), "Author IDs should be a JSON array"
        assert len(author_ids) == 2, "Should have exactly 2 author IDs"

        # Test search by both authors
        author1_search = await async_session.execute(
            text("""
                SELECT work_id 
                FROM search_view_v1 
                WHERE document @@ plainto_tsquery('english', :query)
                AND work_id = :work_id
            """),
            {"query": first1, "work_id": multi_author_work.id},
        )
        assert len(author1_search.fetchall()) == 1, "Should find work by first author"

        author2_search = await async_session.execute(
            text("""
                SELECT work_id 
                FROM search_view_v1 
                WHERE document @@ plainto_tsquery('english', :query)
                AND work_id = :work_id
            """),
            {"query": first2, "work_id": multi_author_work.id},
        )
        assert len(author2_search.fetchall()) == 1, "Should find work by second author"

    async def test_search_view_performance_with_large_dataset(
        self, async_session: AsyncSession, cleanup_test_data
    ):
        """Test materialized view performance with multiple works."""
        test_titles, test_author_names, test_series_names = cleanup_test_data

        import time

        # Create multiple works for performance testing
        batch_size = 20
        test_works = []

        for i in range(batch_size):
            title = f"Performance Test Book {i} {uuid.uuid4().hex[:6]}"
            author_name = f"Perf Author {i} {uuid.uuid4().hex[:4]}"
            first_name, last_name = author_name.split(" ", 2)[:2]

            test_titles.append(title)
            test_author_names.append(f"{first_name} {last_name}")

            work = await work_repository.acreate(
                db=async_session,
                obj_in=WorkCreateIn(
                    title=title,
                    subtitle=f"Subtitle for performance test {i}",
                    type=WorkType.BOOK,
                    authors=[
                        AuthorCreateIn(first_name=first_name, last_name=last_name)
                    ],
                ),
            )
            test_works.append(work)

        # Time the materialized view refresh
        start_time = time.time()
        await async_session.execute(text("REFRESH MATERIALIZED VIEW search_view_v1"))
        await async_session.commit()
        refresh_time = time.time() - start_time

        # Verify all works appear in the view
        count_result = await async_session.execute(
            text(
                """
                SELECT COUNT(*) FROM search_view_v1 
                WHERE work_id IN ({})
            """.format(",".join([str(work.id) for work in test_works]))
            )
        )
        count = count_result.scalar()
        assert count == batch_size, f"Expected {batch_size} works in view, got {count}"

        # Test search performance
        start_time = time.time()
        search_result = await async_session.execute(
            text("""
                SELECT work_id, ts_rank(document, plainto_tsquery('english', :query)) as rank
                FROM search_view_v1 
                WHERE document @@ plainto_tsquery('english', :query)
                ORDER BY rank DESC
                LIMIT 10
            """),
            {"query": "Performance Test"},
        )
        search_time = time.time() - start_time

        search_rows = search_result.fetchall()
        assert len(search_rows) > 0, "Should find performance test works"

        # Performance assertions (should be reasonably fast)
        assert refresh_time < 5.0, (
            f"Materialized view refresh took too long: {refresh_time}s"
        )
        assert search_time < 1.0, f"Search query took too long: {search_time}s"

        logger.info(f"Materialized view refresh time: {refresh_time:.3f}s")
        logger.info(f"Search query time: {search_time:.3f}s")
        logger.info(f"Found {len(search_rows)} matching works")

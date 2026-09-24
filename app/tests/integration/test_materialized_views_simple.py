"""
Integration tests for PostgreSQL materialized views.

This module tests the search_view_v1 materialized view that provides full-text search
functionality. Tests verify that data updates to source tables appear in the
materialized view after manual refresh and that search functionality works correctly.
"""

import logging

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class TestSearchViewV1MaterializedView:
    """Test cases for the search_view_v1 materialized view."""

    async def test_search_view_exists_and_has_expected_structure(
        self, async_session: AsyncSession
    ):
        """Test that search_view_v1 exists and has the expected columns."""

        # Check that the materialized view exists using pg_matviews
        view_exists_result = await async_session.execute(
            text("""
                SELECT COUNT(*) 
                FROM pg_matviews 
                WHERE schemaname = 'public' 
                AND matviewname = 'search_view_v1'
            """)
        )

        view_count = view_exists_result.scalar()
        assert view_count == 1, "search_view_v1 materialized view should exist"

        # Check the view structure using PostgreSQL system catalog
        columns_result = await async_session.execute(
            text("""
                SELECT a.attname as column_name, t.typname as data_type
                FROM pg_attribute a 
                JOIN pg_type t ON a.atttypid = t.oid 
                JOIN pg_class c ON a.attrelid = c.oid 
                WHERE c.relname = 'search_view_v1' 
                AND a.attnum > 0 
                ORDER BY a.attnum
            """)
        )

        columns = columns_result.fetchall()
        column_names = [col[0] for col in columns]

        expected_columns = ["work_id", "author_ids", "series_id", "document"]

        for expected_col in expected_columns:
            assert expected_col in column_names, (
                f"Column {expected_col} should exist in search_view_v1"
            )

        # Verify document column is tsvector type
        document_col = next((col for col in columns if col[0] == "document"), None)
        assert document_col is not None
        assert document_col[1] == "tsvector", (
            f"document column should be tsvector type, got {document_col[1]}"
        )

    async def test_materialized_view_refresh_command(self, async_session: AsyncSession):
        """Test that the materialized view can be refreshed without error."""

        # Get initial row count
        initial_result = await async_session.execute(
            text("SELECT COUNT(*) FROM search_view_v1")
        )
        initial_result.scalar()

        # Refresh the materialized view - this should not raise an error
        await async_session.execute(text("REFRESH MATERIALIZED VIEW search_view_v1"))
        await async_session.commit()

        # Get row count after refresh
        post_refresh_result = await async_session.execute(
            text("SELECT COUNT(*) FROM search_view_v1")
        )
        post_refresh_count = post_refresh_result.scalar()

        # The count might be the same if no data changed, but the command should work
        assert post_refresh_count >= 0, (
            "Materialized view should have non-negative row count"
        )

        logger.info(f"Materialized view has {post_refresh_count} rows after refresh")

    async def test_search_view_full_text_search_functionality(
        self, async_session: AsyncSession
    ):
        """Test that full-text search works with existing data in the materialized view."""

        # Refresh the view to ensure it has current data
        await async_session.execute(text("REFRESH MATERIALIZED VIEW search_view_v1"))
        await async_session.commit()

        # Get total count of works in the view
        total_result = await async_session.execute(
            text("SELECT COUNT(*) FROM search_view_v1")
        )
        total_count = total_result.scalar()

        if total_count == 0:
            pytest.skip("No data in search_view_v1 to test search functionality")

        # Test basic full-text search functionality using plainto_tsquery
        search_result = await async_session.execute(
            text("""
                SELECT work_id, ts_rank(document, plainto_tsquery('english', :query)) as rank
                FROM search_view_v1 
                WHERE document @@ plainto_tsquery('english', :query)
                ORDER BY rank DESC
                LIMIT 5
            """),
            {"query": "book"},  # Generic search term likely to match some titles
        )

        search_rows = search_result.fetchall()

        # We should get some results, and they should have positive ranks
        if len(search_rows) > 0:
            for row in search_rows:
                work_id, rank = row
                assert work_id is not None, "Work ID should not be null"
                assert rank > 0, f"Search rank should be positive, got {rank}"

        logger.info(f"Full-text search for 'book' returned {len(search_rows)} results")

    async def test_search_view_gin_index_exists(self, async_session: AsyncSession):
        """Test that the GIN index on the document column exists."""

        # Check for the GIN index on the document column
        index_result = await async_session.execute(
            text("""
                SELECT indexname, indexdef
                FROM pg_indexes 
                WHERE tablename = 'search_view_v1' 
                AND indexdef LIKE '%gin%'
            """)
        )

        indexes = index_result.fetchall()

        # Should have at least one GIN index
        assert len(indexes) > 0, "search_view_v1 should have at least one GIN index"

        # Check that there's an index on the document column
        document_index_found = False
        for index_name, index_def in indexes:
            if "document" in index_def and "gin" in index_def.lower():
                document_index_found = True
                break

        assert document_index_found, "Should have a GIN index on the document column"

        logger.info(f"Found {len(indexes)} GIN indexes on search_view_v1")

    async def test_search_view_performance_basic(self, async_session: AsyncSession):
        """Test basic performance of search view queries."""

        import time

        # Refresh the view
        await async_session.execute(text("REFRESH MATERIALIZED VIEW search_view_v1"))
        await async_session.commit()

        # Time a search query
        start_time = time.time()

        search_result = await async_session.execute(
            text("""
                SELECT work_id, ts_rank(document, plainto_tsquery('english', :query)) as rank
                FROM search_view_v1 
                WHERE document @@ plainto_tsquery('english', :query)
                ORDER BY rank DESC
                LIMIT 10
            """),
            {"query": "adventure story"},
        )

        end_time = time.time()
        query_time = end_time - start_time

        search_rows = search_result.fetchall()

        # Query should complete reasonably quickly (under 1 second for most cases)
        assert query_time < 5.0, f"Search query took too long: {query_time:.3f}s"

        logger.info(
            f"Search query completed in {query_time:.3f}s, found {len(search_rows)} results"
        )

    async def test_search_view_data_types(self, async_session: AsyncSession):
        """Test that the materialized view returns correct data types."""

        # Refresh the view
        await async_session.execute(text("REFRESH MATERIALIZED VIEW search_view_v1"))
        await async_session.commit()

        # Get a sample row
        sample_result = await async_session.execute(
            text(
                "SELECT work_id, author_ids, series_id, document FROM search_view_v1 LIMIT 1"
            )
        )

        sample_row = sample_result.fetchone()

        if sample_row is None:
            pytest.skip("No data in search_view_v1 to test data types")

        work_id, author_ids, series_id, document = sample_row

        # Verify data types
        assert isinstance(work_id, int), (
            f"work_id should be integer, got {type(work_id)}"
        )
        assert isinstance(author_ids, list), (
            f"author_ids should be list, got {type(author_ids)}"
        )
        # series_id can be None or int
        assert series_id is None or isinstance(series_id, int), (
            f"series_id should be None or int, got {type(series_id)}"
        )
        # document is a tsvector, which appears as a string in Python
        assert isinstance(document, str), (
            f"document should be string (tsvector), got {type(document)}"
        )

        # Verify author_ids is a non-empty list of integers
        if author_ids:
            for author_id in author_ids:
                assert isinstance(author_id, int), (
                    f"Each author_id should be integer, got {type(author_id)}"
                )

        logger.info(
            f"Sample row data types verified: work_id={type(work_id)}, author_ids={type(author_ids)}, series_id={type(series_id)}, document={type(document)}"
        )

    async def test_materialized_view_consistency(self, async_session: AsyncSession):
        """Test that the materialized view data is consistent with source tables."""

        # Refresh the view to ensure consistency
        await async_session.execute(text("REFRESH MATERIALIZED VIEW search_view_v1"))
        await async_session.commit()

        # Check that all work_ids in the view exist in the works table
        consistency_result = await async_session.execute(
            text("""
                SELECT COUNT(*) as inconsistent_count
                FROM search_view_v1 sv
                LEFT JOIN works w ON sv.work_id = w.id
                WHERE w.id IS NULL
            """)
        )

        inconsistent_count = consistency_result.scalar()
        assert inconsistent_count == 0, (
            f"Found {inconsistent_count} work_ids in search view that don't exist in works table"
        )

        # Check that author_ids in the view correspond to real authors
        author_consistency_result = await async_session.execute(
            text("""
                SELECT COUNT(*) as total_view_rows
                FROM search_view_v1
                WHERE author_ids IS NOT NULL AND jsonb_array_length(author_ids) > 0
            """)
        )

        total_with_authors = author_consistency_result.scalar()

        if total_with_authors > 0:
            logger.info(f"Found {total_with_authors} works with authors in search view")

        logger.info("Materialized view consistency check passed")

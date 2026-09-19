#!/usr/bin/env python3
"""
Integration tests for session management and connection pooling.
Tests the modernized session management under various scenarios.

NOTE: These tests are skipped in the regular test suite because they test
production pool behavior by creating multiple engines with their own pools,
which can exhaust database connections when run alongside other tests.
Run these tests in isolation with: pytest --run-isolated-tests
"""

import concurrent.futures
import time

import pytest
from sqlalchemy import text

from app.db.session import get_session, get_session_maker

# Mark all tests in this module as requiring isolation
pytestmark = pytest.mark.isolated


@pytest.fixture(autouse=True)
def wait_for_pool_availability():
    """Wait for connection pool to have available connections before each test.

    This helps prevent flaky failures when tests run in sequence and the pool
    is temporarily exhausted from previous tests.
    """
    # Give a brief pause for any outstanding connections to be returned
    time.sleep(0.05)
    yield
    # Allow cleanup after test
    time.sleep(0.05)


class TestSessionManagement:
    """Test session cleanup and connection pooling behavior."""

    def test_session_cleanup(self, session):
        """Test that sessions are properly cleaned up after use."""
        get_session_maker()

        # Create and use multiple sessions sequentially (reduced from 5 to 3)
        for i in range(3):
            session_gen = get_session()
            session = next(session_gen)

            # Use the session
            result = session.execute(text("SELECT 1")).scalar()
            assert result == 1

            # Close the session
            try:
                next(session_gen)
            except StopIteration:
                pass  # Expected

    def test_connection_pooling(self, session):
        """Test connection pool behavior and limits."""
        session_maker = get_session_maker()
        test_session = session_maker()
        engine = test_session.get_bind()
        test_session.close()
        pool = engine.pool

        # Get pool info
        pool.checkedout()

        def use_session(session_id):
            try:
                session_gen = get_session()
                session = next(session_gen)

                # Hold the session briefly
                session.execute(text(f"SELECT {session_id}, pg_sleep(0.1)")).scalar()

                # Close session
                try:
                    next(session_gen)
                except StopIteration:
                    pass

                return f"Session {session_id}: OK"
            except Exception as e:
                return f"Session {session_id}: ERROR - {e}"

        # Test concurrent usage within pool limits (should be 10 by default)
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(use_session, i) for i in range(5)]
            results = [future.result(timeout=5) for future in futures]

        success_count = sum(1 for r in results if "OK" in r)
        assert success_count == 5, (
            f"Expected 5 successful sessions, got {success_count}"
        )

    def test_pool_exhaustion_recovery(self, session):
        """Test behavior when connection pool approaches limits."""
        # Create several sessions without overwhelming the pool
        sessions = []
        session_gens = []

        # Try to create sessions up to a reasonable limit
        for i in range(8):  # Less than default pool size of 10
            try:
                session_gen = get_session()
                session = next(session_gen)
                sessions.append(session)
                session_gens.append(session_gen)

                # Quick test that session works
                result = session.execute(text("SELECT 1")).scalar()
                assert result == 1

            except Exception as e:
                pytest.fail(f"Session {i} failed unexpectedly: {e}")

        # Clean up all sessions
        for session_gen in session_gens:
            try:
                next(session_gen)
            except StopIteration:
                pass

        # Test that new sessions work after cleanup
        time.sleep(0.1)

        session_gen = get_session()
        session = next(session_gen)
        result = session.execute(text("SELECT 'recovery_test'")).scalar()
        assert result == "recovery_test"

        try:
            next(session_gen)
        except StopIteration:
            pass

    def test_session_error_cleanup(self, session):
        """Test that sessions are cleaned up even when errors occur."""
        # Test session cleanup with various error conditions
        error_scenarios = [
            "SELECT * FROM non_existent_table_12345",  # Table doesn't exist
            "INVALID SQL SYNTAX HERE",  # Syntax error
        ]

        for i, bad_sql in enumerate(error_scenarios):
            session_gen = get_session()
            session = next(session_gen)

            with pytest.raises(Exception):  # Expect SQL errors
                session.execute(text(bad_sql)).scalar()

            # Session should still clean up properly
            try:
                next(session_gen)
            except StopIteration:
                pass

    def test_concurrent_session_stress(self, session):
        """Stress test with multiple concurrent sessions."""

        def stress_worker(worker_id):
            """Worker function that creates and uses sessions."""
            try:
                results = []
                for i in range(5):  # Each worker creates 5 sessions
                    session_gen = get_session()
                    session = next(session_gen)

                    # Do some work
                    result = session.execute(
                        text(f"SELECT {worker_id * 100 + i}")
                    ).scalar()
                    results.append(result)

                    # Clean up
                    try:
                        next(session_gen)
                    except StopIteration:
                        pass

                    # Brief pause to simulate real work
                    time.sleep(0.01)

                return f"Worker {worker_id}: {len(results)} sessions OK"

            except Exception as e:
                return f"Worker {worker_id}: ERROR - {e}"

        # Run stress test with multiple workers
        num_workers = 3

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = [executor.submit(stress_worker, i) for i in range(num_workers)]
            results = [future.result(timeout=10) for future in futures]

        success_count = sum(1 for r in results if "OK" in r)
        assert success_count == num_workers, (
            f"Expected {num_workers} successful workers, got {success_count}"
        )

    def test_session_context_manager(self, session):
        """Test that session context manager works correctly."""
        session_factory = get_session_maker()

        # Test normal usage
        with session_factory() as session:
            result = session.execute(text("SELECT 1")).scalar()
            assert result == 1

        # Test with exception
        try:
            with session_factory() as session:
                session.execute(text("SELECT 1")).scalar()
                raise ValueError("Test exception")
        except ValueError:
            pass  # Expected

        # Session should still be cleaned up properly
        with session_factory() as session:
            result = session.execute(text("SELECT 'after_exception'")).scalar()
            assert result == "after_exception"

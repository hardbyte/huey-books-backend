import asyncio
import logging
import os
import random
import secrets
import signal
import time
from datetime import timedelta
from pathlib import Path

# Disable event listener in tests to prevent connection pool exhaustion
# Must be set BEFORE importing app modules that load settings
os.environ["DISABLE_EVENT_LISTENER"] = "true"

import psutil
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from starlette.testclient import TestClient

# Set up verbose logging for debugging test setup failures
logger = logging.getLogger(__name__)


@pytest.fixture(autouse=True)
def detect_blocking_calls():
    """Fail tests that make blocking calls on the asyncio event loop.

    The API serves concurrency=20 per Cloud Run instance, so one synchronous
    blocking call (a sync SDK, file, or socket op inside an async handler)
    stalls every other coroutine on that instance. blockbuster monkeypatches
    known-blocking stdlib calls to raise when invoked inside the running loop.

    Off by default because it surfaces pre-existing blocking calls that must be
    offloaded first; enable with BLOCKBUSTER=1 to hunt them down.
    """
    if not os.environ.get("BLOCKBUSTER"):
        yield
        return

    from blockbuster import blockbuster_ctx

    with blockbuster_ctx():
        yield


# Global engine cache to prevent creating too many engines
# Note: Only cache sync engines due to asyncio event loop conflicts with async engines
_test_engine_cache = {}

from app import crud
from app.api.dependencies.security import create_user_access_token
from app.db.session import database_connection, get_session_maker
from app.main import app, get_settings
from app.models import (
    Collection,
    Edition,
    School,
    SchoolState,
    ServiceAccountType,
    Student,
)
from app.models.class_group import ClassGroup
from app.models.user import UserAccountType
from app.models.work import WorkType
from app.repositories.author_repository import author_repository
from app.repositories.class_group_repository import class_group_repository
from app.repositories.edition_repository import edition_repository
from app.repositories.product_repository import product_repository
from app.repositories.school_repository import school_repository
from app.repositories.service_account_repository import service_account_repository
from app.repositories.work_repository import work_repository
from app.schemas.author import AuthorCreateIn
from app.schemas.collection import (
    CollectionAndItemsUpdateIn,
    CollectionCreateIn,
    CollectionItemCreateIn,
    CollectionItemUpdate,
    CollectionUpdateType,
)
from app.schemas.edition import EditionCreateIn
from app.schemas.product import ProductCreateIn
from app.schemas.recommendations import HueKeys, ReadingAbilityKey
from app.schemas.service_account import ServiceAccountCreateIn
from app.schemas.users.huey_attributes import HueyAttributes
from app.schemas.users.user_create import UserCreateIn
from app.schemas.work import WorkCreateIn
from app.services.collections import reset_collection
from app.services.editions import generate_random_valid_isbn13
from app.services.security import create_access_token
from app.tests.util.random_strings import random_lower_string


@pytest.fixture(scope="function")
def client():
    """Create a fresh TestClient for each test function to prevent state leakage.

    Changed from module to function scope to match database session scope
    and prevent resource accumulation issues.
    """
    with TestClient(app) as c:
        # This is because we want to keep debugging tests for longer but the agent
        # has a rate limit.
        # Only sleep if explicitly requested via environment variable
        if os.getenv("TEST_DEBUG_SLEEP") == "true":
            time.sleep(60)

        yield c


@pytest.fixture(
    scope="session",
    params=[
        pytest.param(("asyncio", {"use_uvloop": True}), id="asyncio+uvloop"),
    ],
)
def anyio_backend(request):
    return request.param


@pytest.fixture(scope="module")
def test_data_path():
    return Path(__file__).parent.parent / "data"


@pytest.fixture(scope="session")
def settings():
    yield get_settings()

    # Clean up cached engines at end of session
    logger.debug("Cleaning up cached engines at session end")

    # Clean up sync engines
    for cache_key, (engine, _) in _test_engine_cache.items():
        try:
            engine.dispose()
            logger.debug(f"Disposed cached sync engine: {cache_key}")
        except Exception as e:
            logger.warning(f"Error disposing cached sync engine {cache_key}: {e}")

    _test_engine_cache.clear()


@pytest.fixture(scope="session")
def test_app() -> FastAPI:
    """Create a test app with overridden dependencies."""
    # app.dependency_overrides[get_db_session] = lambda: db_session

    return app


@pytest.fixture
async def async_client(test_app, reset_global_state):
    """AsyncClient for testing with proper event listener cleanup.

    Depends on reset_global_state to ensure event listener connections
    are properly cleaned up between tests.
    """
    from httpx import ASGITransport

    logger.debug("Creating async HTTP client for testing")
    client = None

    try:
        # Create client with timeout protection
        client = AsyncClient(
            transport=ASGITransport(app=test_app),
            base_url="http://test",
            timeout=30.0,  # 30 second timeout for HTTP requests
        )

        await asyncio.wait_for(client.__aenter__(), timeout=10.0)
        logger.debug("Successfully created async client")

        yield client
        logger.debug("Async client context manager exiting")

    except asyncio.TimeoutError:
        logger.error("Async client creation/operation timed out")
        raise
    except Exception as e:
        logger.error(f"Error creating async client: {e}")
        raise
    finally:
        # Ensure proper cleanup
        if client:
            try:
                await asyncio.wait_for(client.__aexit__(None, None, None), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("Async client cleanup timed out")
            except Exception as e:
                logger.warning(f"Error cleaning up async client: {e}")


@pytest.fixture
async def internal_async_client(reset_global_state):
    """AsyncClient for the internal API with timeout protection.

    Depends on reset_global_state to ensure event listener connections
    are properly cleaned up between tests.
    """
    from httpx import ASGITransport

    from app.internal_api import internal_app

    client = None

    try:
        client = AsyncClient(
            transport=ASGITransport(app=internal_app),
            base_url="http://test",
            timeout=30.0,  # 30 second timeout for HTTP requests
        )

        await asyncio.wait_for(client.__aenter__(), timeout=10.0)
        yield client

    except asyncio.TimeoutError:
        logger.error("Internal async client operation timed out")
        raise
    except Exception as e:
        logger.error(f"Error with internal async client: {e}")
        raise
    finally:
        if client:
            try:
                await asyncio.wait_for(client.__aexit__(None, None, None), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("Internal async client cleanup timed out")
            except Exception as e:
                logger.warning(f"Error cleaning up internal async client: {e}")


@pytest.fixture()
def session(settings, reset_global_state_sync):
    """Create a clean database session for each test with proper cleanup and timeouts."""

    def timeout_handler(signum, frame):
        raise TimeoutError("Database session operation timed out")

    old_handler = signal.signal(signal.SIGALRM, timeout_handler)
    session = None

    try:
        # Create session with timeout protection
        signal.alarm(30)  # 30 second timeout
        session_maker = get_session_maker(settings)
        session = session_maker()
        signal.alarm(0)  # Cancel timeout

        yield session

    except TimeoutError:
        logger.error("Database session creation timed out")
        raise
    finally:
        # Ensure proper cleanup with timeout protection
        if session:
            try:
                signal.alarm(10)  # 10 second timeout for cleanup
                # Rollback any uncommitted changes
                session.rollback()
            except TimeoutError:
                logger.warning("Session rollback timed out")
            except Exception as e:
                logger.warning(f"Error during session rollback: {e}")
            finally:
                try:
                    session.close()
                    signal.alarm(0)
                except TimeoutError:
                    logger.warning("Session close timed out")
                except Exception as e:
                    logger.warning(f"Error closing session: {e}")
                finally:
                    signal.alarm(0)

        # Restore signal handler
        signal.signal(signal.SIGALRM, old_handler)


@pytest.fixture()
def reset_global_state_sync():
    """Reset all global singletons and state before each test (sync version) with timeout protection."""
    import signal

    def timeout_handler(signum, frame):
        raise TimeoutError("Global state reset timed out")

    # Set up timeout protection
    old_handler = signal.signal(signal.SIGALRM, timeout_handler)

    try:
        # Reset singletons to ensure test isolation with timeout
        signal.alarm(30)  # 30 second timeout

        try:
            from app.db.session import reset_async_session_makers
            from app.repositories.chat_repository import reset_chat_repository
            from app.services.api_client import reset_api_client
            from app.services.chat_runtime import reset_chat_runtime
            from app.services.event_listener import reset_event_listener
            from app.services.webhook_notifier import reset_webhook_notifier

            logger.debug("Starting sync global state reset...")
            reset_async_session_makers()
            reset_event_listener()
            logger.debug("Reset event listener (sync)")
            reset_chat_runtime()
            logger.debug("Reset chat runtime (sync)")
            reset_chat_repository()
            logger.debug("Reset chat repository (sync)")
            reset_api_client()
            logger.debug("Reset API client (sync)")
            reset_webhook_notifier()
            logger.debug("Reset webhook notifier (sync)")

            logger.debug("✅ Completed sync global state reset for test isolation")
        except TimeoutError:
            logger.error("Global state reset timed out during setup")
        except Exception as e:
            logger.warning(f"Error resetting global state: {e}")
        finally:
            signal.alarm(0)  # Cancel timeout

        yield

        # Clean up after test with timeout
        signal.alarm(30)  # 30 second timeout
        try:
            from app.db.session import reset_async_session_makers

            reset_event_listener()
            reset_chat_runtime()
            reset_chat_repository()
            reset_api_client()
            reset_webhook_notifier()
            reset_async_session_makers()
            logger.debug("Cleaned up global state after test (sync)")
        except TimeoutError:
            logger.error("Global state cleanup timed out")
        except Exception as e:
            logger.warning(f"Error cleaning up global state: {e}")
        finally:
            signal.alarm(0)  # Cancel timeout

    finally:
        # Restore original signal handler
        signal.signal(signal.SIGALRM, old_handler)


@pytest.fixture()
async def reset_global_state():
    """Reset all global singletons and state before each test (async version) with timeout protection."""
    import asyncio

    # Reset singletons to ensure test isolation with timeout
    try:

        async def reset_state():
            from app.db.session import reset_async_session_makers
            from app.repositories.chat_repository import reset_chat_repository
            from app.services.api_client import reset_api_client
            from app.services.chat_runtime import reset_chat_runtime
            from app.services.event_listener import reset_event_listener
            from app.services.webhook_notifier import reset_webhook_notifier

            logger.debug("Starting global state reset...")
            reset_async_session_makers()
            reset_event_listener()
            logger.debug("Reset event listener")
            reset_chat_runtime()
            logger.debug("Reset chat runtime")
            reset_chat_repository()
            logger.debug("Reset chat repository")
            reset_api_client()
            logger.debug("Reset API client")
            reset_webhook_notifier()
            logger.debug("Reset webhook notifier")

            logger.debug("✅ Completed global state reset for test isolation")

        # Run with timeout
        await asyncio.wait_for(reset_state(), timeout=30.0)

    except asyncio.TimeoutError:
        logger.error("Global state reset timed out during setup")
    except Exception as e:
        logger.warning(f"Error resetting global state: {e}")

    yield

    # Clean up after test with timeout
    try:

        async def cleanup_state():
            from app.db.session import reset_async_session_makers
            from app.repositories.chat_repository import reset_chat_repository
            from app.services.api_client import reset_api_client
            from app.services.chat_runtime import reset_chat_runtime
            from app.services.event_listener import reset_event_listener
            from app.services.webhook_notifier import reset_webhook_notifier

            reset_event_listener()
            reset_chat_runtime()
            reset_chat_repository()
            reset_api_client()
            reset_webhook_notifier()
            reset_async_session_makers()
            logger.debug("Cleaned up global state after test")

        await asyncio.wait_for(cleanup_state(), timeout=30.0)

    except asyncio.TimeoutError:
        logger.error("Global state cleanup timed out")
    except Exception as e:
        logger.warning(f"Error cleaning up global state: {e}")


@pytest.fixture()
async def async_session(reset_global_state):
    """Create an isolated async session for each test with proper cleanup and timeouts."""
    import asyncio
    import os

    test_name = (
        os.environ.get("PYTEST_CURRENT_TEST", "unknown").split("::")[-1].split(" ")[0]
    )

    logger.debug(f"🔧 [DEBUG] Creating async session for test: {test_name}")
    logger.debug(
        f"🔧 [DEBUG] Process memory: {psutil.Process().memory_info().rss / 1024 / 1024:.1f}MB"
    )
    logger.debug(f"🔧 [DEBUG] Active async tasks: {len(asyncio.all_tasks())}")

    session = None
    session_factory = None
    engine = None

    try:
        # Create session factory with smaller pool for tests
        from app.config import get_settings

        settings = get_settings()

        # Create fresh engine for each test to avoid asyncio event loop conflicts
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        # Always create fresh async engine to avoid event loop conflicts
        engine = create_async_engine(
            settings.SQLALCHEMY_ASYNC_URI,
            pool_size=1,  # Minimal pool for tests - only one connection per test
            max_overflow=0,  # No overflow to prevent connection leaks
            pool_recycle=30,  # Very short recycle time for tests (30 seconds)
            pool_timeout=2,  # Very short timeout for tests (2 seconds)
            pool_pre_ping=True,  # Verify connections before use
            echo=False,  # Disable SQL logging for tests
            # Additional settings for better test isolation
            connect_args={
                "command_timeout": 10,  # 10 second query timeout
                "server_settings": {
                    "application_name": f"wriveted_test_{test_name}",
                    "jit": "off",  # Disable JIT for faster test execution
                },
            },
        )
        logger.debug(f"🔧 [DEBUG] Created fresh async engine for test: {test_name}")
        logger.debug(f"🔧 [DEBUG] Engine pool status: {engine.pool.status()}")

        session_factory = async_sessionmaker(
            engine, autoflush=False, autocommit=False, expire_on_commit=False
        )

        logger.debug(f"🔧 [DEBUG] Created async session factory for: {test_name}")

        session = session_factory()
        logger.debug(
            f"🔧 [DEBUG] Created async session: {session} for test: {test_name}"
        )

        # Test session connectivity with timeout and pool monitoring
        try:
            import asyncio

            from sqlalchemy import text

            # Log pool status before test
            pool = engine.pool
            logger.debug(
                f"Pool status before test - Size: {pool.size()}, Checked in: {pool.checkedin()}, Checked out: {pool.checkedout()}, Overflow: {pool.overflow()}"
            )

            result = await asyncio.wait_for(
                session.execute(text("SELECT 1")),
                timeout=5.0,  # Reduced to 5 second timeout
            )
            logger.debug("Session connectivity test successful")
        except asyncio.TimeoutError:
            logger.error("Session connectivity test timed out")
            raise
        except Exception as e:
            logger.error(f"Session connectivity test failed: {e}")
            raise

        yield session
        logger.debug(
            f"🔧 [DEBUG] Test completed, starting session cleanup for: {test_name}"
        )
        logger.debug(
            f"🔧 [DEBUG] Process memory after test: {psutil.Process().memory_info().rss / 1024 / 1024:.1f}MB"
        )
        logger.debug(
            f"🔧 [DEBUG] Active async tasks after test: {len(asyncio.all_tasks())}"
        )

    except Exception as e:
        logger.error(f"🔧 [DEBUG] Error creating async session for {test_name}: {e}")
        raise
    finally:
        # Ensure proper cleanup with timeouts and monitoring
        cleanup_start_time = time.time()
        if session:
            try:
                # Check pool status before cleanup
                if engine:
                    pool = engine.pool
                    logger.debug(
                        f"🔧 [DEBUG] Pool status during cleanup for {test_name} - Size: {pool.size()}, Checked in: {pool.checkedin()}, Checked out: {pool.checkedout()}, Overflow: {pool.overflow()}"
                    )

                # Rollback any uncommitted transactions with timeout
                if session.in_transaction():
                    logger.debug(
                        f"🔧 [DEBUG] Rolling back uncommitted transactions for: {test_name}"
                    )
                    await asyncio.wait_for(session.rollback(), timeout=3.0)
                else:
                    logger.debug(
                        f"🔧 [DEBUG] No transactions to rollback for: {test_name}"
                    )
            except asyncio.TimeoutError:
                logger.warning(
                    f"🔧 [DEBUG] Session rollback timed out for: {test_name}"
                )
            except Exception as e:
                logger.warning(
                    f"🔧 [DEBUG] Error during session rollback for {test_name}: {e}"
                )
            finally:
                # Always close the session with timeout
                try:
                    logger.debug(f"🔧 [DEBUG] Closing async session for: {test_name}")
                    await asyncio.wait_for(session.close(), timeout=3.0)
                    logger.debug(
                        f"🔧 [DEBUG] Successfully closed async session for: {test_name}"
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        f"🔧 [DEBUG] Session close timed out for: {test_name}"
                    )
                except Exception as e:
                    logger.warning(
                        f"🔧 [DEBUG] Error closing session for {test_name}: {e}"
                    )

        # Always dispose async engines since they're not cached
        if engine:
            try:
                # Log pool status before disposal
                pool = engine.pool
                logger.debug(
                    f"🔧 [DEBUG] Pool status before disposal for {test_name} - Size: {pool.size()}, Checked in: {pool.checkedin()}, Checked out: {pool.checkedout()}, Overflow: {pool.overflow()}"
                )

                logger.debug(f"🔧 [DEBUG] Disposing async engine for: {test_name}")
                await asyncio.wait_for(engine.dispose(), timeout=3.0)
                logger.debug(
                    f"🔧 [DEBUG] Successfully disposed async engine for: {test_name}"
                )
            except asyncio.TimeoutError:
                logger.warning(f"🔧 [DEBUG] Engine disposal timed out for: {test_name}")
            except Exception as e:
                logger.warning(
                    f"🔧 [DEBUG] Error disposing engine for {test_name}: {e}"
                )

        cleanup_duration = time.time() - cleanup_start_time
        logger.debug(
            f"🔧 [DEBUG] Cleanup completed for {test_name} in {cleanup_duration:.2f}s"
        )
        logger.debug(
            f"🔧 [DEBUG] Final process memory: {psutil.Process().memory_info().rss / 1024 / 1024:.1f}MB"
        )


@pytest.fixture()
def session_factory(settings):
    """Create session factory for each test with proper engine management and timeouts."""
    import signal

    def timeout_handler(signum, frame):
        raise TimeoutError("Database operation timed out")

    old_handler = signal.signal(signal.SIGALRM, timeout_handler)
    engine = None

    try:
        # Create or reuse engine with minimal test-specific settings
        signal.alarm(30)  # 30 second timeout for connection

        # Use cached engine if available
        cache_key = str(settings.SQLALCHEMY_DATABASE_URI)
        if cache_key in _test_engine_cache:
            engine, SessionMaker = _test_engine_cache[cache_key]
            logger.debug("Reusing cached sync engine")
        else:
            engine, SessionMaker = database_connection(
                settings.SQLALCHEMY_DATABASE_URI,
                pool_size=1,  # Minimal pool for tests
                max_overflow=0,  # No overflow to prevent connection leaks
            )
            _test_engine_cache[cache_key] = (engine, SessionMaker)
            logger.debug("Created new cached sync engine")

        signal.alarm(0)  # Cancel timeout
        yield SessionMaker

    except TimeoutError:
        logger.error("Database connection creation timed out")
        raise
    finally:
        # Note: We don't dispose the cached engine here to allow reuse
        # The engine will be cleaned up when the test session ends
        if engine and cache_key not in _test_engine_cache:
            try:
                signal.alarm(5)  # 5 second timeout for disposal
                engine.dispose()
                signal.alarm(0)
            except TimeoutError:
                logger.warning("Engine disposal timed out")
            except Exception as e:
                logger.warning(f"Error disposing engine: {e}")
            finally:
                signal.alarm(0)

        # Restore signal handler
        signal.signal(signal.SIGALRM, old_handler)


@pytest.fixture()
def backend_service_account(session):
    sa = service_account_repository.create(
        db=session,
        obj_in=ServiceAccountCreateIn(
            name="backend integration test account",
            type=ServiceAccountType.BACKEND,
        ),
    )
    yield sa

    try:
        session.delete(sa)
        session.commit()
    except Exception:
        session.rollback()


@pytest.fixture()
def test_user_account(session):
    user = crud.user.create(
        db=session,
        obj_in=UserCreateIn(
            name="integration test account (public)",
            email=f"{random_lower_string(6)}@test.com",
            first_name="Test",
            last_name_initial="L",
        ),
    )
    yield user
    try:
        session.delete(user)
        session.commit()
    except Exception:
        session.rollback()


@pytest.fixture()
def test_student_user_account(session, test_school, test_class_group):
    student = crud.user.create(
        db=session,
        obj_in=UserCreateIn(
            name="integration test account (student)",
            email=f"{random_lower_string(6)}@test.com",
            first_name="Test",
            last_name_initial="A",
            type="student",
            school_id=test_school.id,
            class_group_id=test_class_group.id,
            username=random_lower_string(6),
        ),
    )
    yield student
    try:
        session.delete(student)
        session.commit()
    except Exception:
        session.rollback()


@pytest.fixture()
def test_schooladmin_account(test_school, session):
    schooladmin = crud.user.create(
        db=session,
        obj_in=UserCreateIn(
            name="integration test account (school admin)",
            email=f"{random_lower_string(6)}@test.com",
            type=UserAccountType.SCHOOL_ADMIN,
            school_id=test_school.id,
        ),
    )
    yield schooladmin
    try:
        session.delete(schooladmin)
        session.commit()
    except Exception:
        session.rollback()


@pytest.fixture()
def test_wrivetedadmin_account(session):
    wrivetedadmin = crud.user.create(
        db=session,
        obj_in=UserCreateIn(
            name="integration test account (wriveted admin)",
            email=f"{random_lower_string(6)}@test.com",
            type=UserAccountType.WRIVETED,
        ),
    )
    yield wrivetedadmin
    try:
        session.delete(wrivetedadmin)
        session.commit()
    except Exception:
        session.rollback()


@pytest.fixture()
def backend_service_account_token(settings, backend_service_account):
    print("Generating auth token")
    access_token = create_access_token(
        subject=f"wriveted:service-account:{backend_service_account.id}",
        expires_delta=timedelta(
            minutes=settings.SERVICE_ACCOUNT_ACCESS_TOKEN_EXPIRE_MINUTES
        ),
    )
    return access_token


@pytest.fixture()
def test_user_account_token(test_user_account):
    print("Generating auth token")
    access_token = create_access_token(
        subject=f"wriveted:user-account:{test_user_account.id}",
        expires_delta=timedelta(minutes=5),
    )
    return access_token


@pytest.fixture()
def test_schooladmin_account_token(test_schooladmin_account):
    print("Generating auth token")
    access_token = create_access_token(
        subject=f"wriveted:user-account:{test_schooladmin_account.id}",
        expires_delta=timedelta(minutes=5),
    )
    return access_token


@pytest.fixture()
def test_student_user_account_token(test_student_user_account):
    print("Generating auth token")
    access_token = create_access_token(
        subject=f"wriveted:user-account:{test_student_user_account.id}",
        expires_delta=timedelta(minutes=5),
    )
    return access_token


@pytest.fixture()
def test_wrivetedadmin_account_token(test_wrivetedadmin_account):
    print("Generating auth token")
    access_token = create_access_token(
        subject=f"wriveted:user-account:{test_wrivetedadmin_account.id}",
        expires_delta=timedelta(minutes=5),
    )
    return access_token


@pytest.fixture()
def backend_service_account_headers(backend_service_account_token):
    return {"Authorization": f"bearer {backend_service_account_token}"}


@pytest.fixture()
def test_user_account_headers(test_user_account_token):
    return {"Authorization": f"bearer {test_user_account_token}"}


@pytest.fixture()
def test_student_user_account_headers(test_student_user_account_token):
    return {"Authorization": f"bearer {test_student_user_account_token}"}


@pytest.fixture()
def test_schooladmin_account_headers(test_schooladmin_account_token):
    return {"Authorization": f"bearer {test_schooladmin_account_token}"}


@pytest.fixture()
def test_wrivetedadmin_account_headers(test_wrivetedadmin_account_token):
    return {"Authorization": f"bearer {test_wrivetedadmin_account_token}"}


@pytest.fixture
async def async_client_authenticated_as_wriveted_user(
    async_client: AsyncClient, test_wrivetedadmin_account_token
):
    """AsyncClient with Wriveted admin user authentication headers."""
    async_client.headers.update(
        {"Authorization": f"bearer {test_wrivetedadmin_account_token}"}
    )
    return async_client


@pytest.fixture
async def async_client_authenticated_as_service_account(
    async_client: AsyncClient, backend_service_account_token
):
    """AsyncClient with backend service account authentication headers."""
    async_client.headers.update(
        {"Authorization": f"bearer {backend_service_account_token}"}
    )
    return async_client


@pytest.fixture
async def wriveted_admin(async_session):
    """Async fixture for wriveted admin user for use in async tests."""
    from app.models.user import User, UserAccountType

    user = User(
        name="integration test account (wriveted admin)",
        email=f"{random_lower_string(6)}@test.com",
        type=UserAccountType.WRIVETED,
    )
    async_session.add(user)
    await async_session.flush()

    yield user

    try:
        await async_session.delete(user)
        await async_session.commit()
    except Exception:
        await async_session.rollback()


@pytest.fixture()
def author_list(client, session):
    n = 10
    authors = [
        author_repository.create(
            db=session,
            obj_in=AuthorCreateIn(
                first_name=random_lower_string(length=random.randint(2, 12)),
                last_name=random_lower_string(length=random.randint(2, 12)),
            ),
        )
        for _ in range(n)
    ]

    yield authors

    try:
        for a in authors:
            author_repository.remove(db=session, id=a.id)
        session.commit()
    except Exception:
        session.rollback()


@pytest.fixture()
def test_product(session):
    product = product_repository.get_by_id(
        db=session, product_id="integration-test-product"
    )
    if not product:
        product = product_repository.create(
            db=session,
            obj_in=ProductCreateIn(
                name="Super Cool Tier",
                id="integration-test-product",
            ),
        )
    yield product
    try:
        session.delete(product)
        session.commit()
    except Exception:
        session.rollback()


@pytest.fixture()
def small_works_list(client, session, author_list):
    """A smaller, more efficient works list for most tests (25 items)."""
    n = 25

    works = []
    edition_ids = []

    try:
        # Generate unique ISBNs by checking database
        candidate_isbns = [generate_random_valid_isbn13() for _ in range(n * 2)]
        existing_isbns = set(
            session.execute(
                select(Edition.isbn).where(Edition.isbn.in_(candidate_isbns))
            )
            .scalars()
            .all()
        )
        available_isbns = [
            isbn for isbn in candidate_isbns if isbn not in existing_isbns
        ][:n]

        if len(available_isbns) < n:
            raise RuntimeError(f"Could not generate {n} unique ISBNs")

        # Create works more efficiently for smaller lists
        for i in range(n):
            author = random.choice(author_list)
            work_authors = [
                AuthorCreateIn(first_name=author.first_name, last_name=author.last_name)
            ]
            work = work_repository.get_or_create(
                db=session,
                work_data=WorkCreateIn(
                    type=WorkType.BOOK,
                    title=f"Small Test Work {i}_{random_lower_string(4)}",
                    authors=work_authors,
                ),
                authors=[author],
                commit=False,
            )

            edition = edition_repository.create(
                db=session,
                edition_data=EditionCreateIn(
                    isbn=available_isbns[i],
                    title=f"Small Test Edition {i}_{random_lower_string(4)}",
                    cover_url="https://cool.site",
                    info={},
                ),
                work=work,
                illustrators=[],
                commit=False,
            )

            works.append(work)
            edition_ids.append(edition.isbn)

        # Single commit for smaller dataset
        session.commit()
        logger.debug(f"Created {len(works)} works for small_works_list")

        yield works

    finally:
        # Cleanup
        try:
            for isbn in edition_ids:
                try:
                    edition = edition_repository.get(db=session, isbn=isbn)
                    if edition:
                        session.delete(edition)
                except Exception as e:
                    logger.warning(f"Error deleting edition {isbn}: {e}")

            for work in works:
                try:
                    session.refresh(work)
                    work_repository.remove(db=session, id=work.id)
                except Exception as e:
                    logger.warning(f"Error deleting work {work.id}: {e}")

            session.commit()
            logger.debug(
                f"Successfully cleaned up {len(works)} works from small_works_list"
            )

        except Exception as e:
            logger.error(f"Error during small_works_list cleanup: {e}")
            session.rollback()


@pytest.fixture()
def works_list(client, session, author_list):
    """Large works list for tests that specifically need 100 items."""
    n = 100

    works = []
    edition_ids = []

    try:
        # Generate all unique ISBNs upfront by checking database once
        candidate_isbns = [generate_random_valid_isbn13() for _ in range(n * 2)]
        existing_isbns = set(
            session.execute(
                select(Edition.isbn).where(Edition.isbn.in_(candidate_isbns))
            )
            .scalars()
            .all()
        )
        available_isbns = [
            isbn for isbn in candidate_isbns if isbn not in existing_isbns
        ][:n]

        if len(available_isbns) < n:
            raise RuntimeError(f"Could not generate {n} unique ISBNs")

        # Create works in batches for better performance
        batch_size = 10
        isbn_index = 0
        for batch_start in range(0, n, batch_size):
            batch_works = []
            for i in range(batch_start, min(batch_start + batch_size, n)):
                author = random.choice(author_list)
                work_authors = [
                    AuthorCreateIn(
                        first_name=author.first_name, last_name=author.last_name
                    )
                ]
                work = work_repository.get_or_create(
                    db=session,
                    work_data=WorkCreateIn(
                        type=WorkType.BOOK,
                        title=f"Test Work {i}_{random_lower_string(6)}",
                        authors=work_authors,
                    ),
                    authors=[author],
                    commit=False,
                )

                edition = edition_repository.create(
                    db=session,
                    edition_data=EditionCreateIn(
                        isbn=available_isbns[isbn_index],
                        title=f"Test Edition {i}_{random_lower_string(6)}",
                        cover_url="https://cool.site",
                        info={},
                    ),
                    work=work,
                    illustrators=[],
                    commit=False,
                )

                batch_works.append(work)
                edition_ids.append(edition.isbn)
                isbn_index += 1

            # Commit each batch to avoid large transactions
            session.commit()
            works.extend(batch_works)
            logger.debug(
                f"Created batch of {len(batch_works)} works (total: {len(works)})"
            )

        yield works

    finally:
        # Enhanced cleanup with better error handling
        try:
            # Delete editions first (due to foreign key constraints)
            for isbn in edition_ids:
                try:
                    edition = edition_repository.get(db=session, isbn=isbn)
                    if edition:
                        session.delete(edition)
                except Exception as e:
                    logger.warning(f"Error deleting edition {isbn}: {e}")

            # Delete works
            for work in works:
                try:
                    # Refresh the work object to ensure it's still in the session
                    session.refresh(work)
                    work_repository.remove(db=session, id=work.id)
                except Exception as e:
                    logger.warning(f"Error deleting work {work.id}: {e}")

            session.commit()
            logger.debug(
                f"Successfully cleaned up {len(works)} works and {len(edition_ids)} editions"
            )

        except Exception as e:
            logger.error(f"Error during works_list cleanup: {e}")
            session.rollback()


@pytest.fixture()
def test_school(client, session, backend_service_account_headers) -> School:
    # Creating a test school (we could do this directly e.g. using crud or the api)
    test_school_id = secrets.token_hex(8)

    new_test_school_response = client.post(
        "/v1/school",
        headers=backend_service_account_headers,
        json={
            "name": f"Test School - {test_school_id}",
            "country_code": "ATA",
            "official_identifier": test_school_id,
            "info": {
                "msg": "Created for test purposes",
                "location": {"state": "Required", "postcode": "Required"},
            },
        },
        timeout=120,
    )
    new_test_school_response.raise_for_status()
    school_info = new_test_school_response.json()
    # yield SchoolDetail(**school_info)

    print("Yielding from school fixture")
    # Actually lets return the orm object to the tests
    school = school_repository.get_by_wriveted_id_or_404(
        db=session, wriveted_id=school_info["wriveted_identifier"]
    )
    school.state = SchoolState.ACTIVE
    session.add(school)
    session.commit()

    school_id = school.id
    yield school
    print("Cleaning up school fixture")
    # Afterwards delete it

    session.rollback()
    if school_repository.get(session, id=school_id) is not None:
        school_repository.remove(db=session, obj_in=school)


@pytest.fixture()
def test_class_group(
    client, session, backend_service_account_headers, test_school
) -> ClassGroup:
    print("Fixture to create class group")
    new_test_class_response = client.post(
        f"/v1/school/{test_school.wriveted_identifier}/class",
        headers=backend_service_account_headers,
        json={"name": "Test Class", "school_id": str(test_school.wriveted_identifier)},
        timeout=120,
    )
    print(new_test_class_response.status_code)
    new_test_class_response.raise_for_status()
    class_info = new_test_class_response.json()
    print("Yielding from group fixture", class_info)
    yield class_group_repository.get_by_id(db=session, class_group_id=class_info["id"])

    print("Cleaning up group fixture")
    # Afterwards delete it
    client.delete(
        f"/v1/class/{class_info['id']}",
        headers=backend_service_account_headers,
    )


@pytest.fixture()
def test_school_with_students(client, session, test_school, test_class_group) -> School:
    for i in range(100):
        student = Student(
            name=f"Test Student {i}",
            email=f"teststudent-{i}@test.com",
            type=UserAccountType.STUDENT,
            school_id=test_school.id,
            first_name=f"Test-{i}",
            last_name_initial="T",
            class_group_id=test_class_group.id,
        )
        session.add(student)
        session.flush()
    return test_school


@pytest.fixture()
def test_isbns():
    return [
        "9780007453573",
        "9780141321288",
        "9780008197049",
        "9780008355050",
        "9780734410672",
        "9780143782797",
        "9780143308591",
        "9780006754008",
    ]


@pytest.fixture()
def test_unhydrated_editions(client, session, test_isbns):
    # Create a few editions
    editions = [
        edition_repository.get_or_create_unhydrated(db=session, isbn=isbn)
        for isbn in test_isbns
    ]

    yield editions

    try:
        for e in editions:
            edition_repository.remove(db=session, id=e.isbn)
        session.commit()
    except Exception:
        session.rollback()


@pytest.fixture()
def test_user_empty_collection(
    client,
    session,
    test_user_account,
    test_user_account_headers,
) -> Collection:
    collection, created = crud.collection.get_or_create(
        db=session,
        collection_data=CollectionCreateIn(
            name=f"Test Collection {random_lower_string(length=8)}",
            user_id=test_user_account.id,
            info={"msg": "Created for test purposes"},
        ),
    )
    yield collection
    try:
        crud.collection.remove(db=session, id=collection.id)
        session.commit()
    except Exception:
        session.rollback()


@pytest.fixture()
def test_user_collection(
    client, session, test_user_empty_collection: Collection, test_unhydrated_editions
):
    # Add items to existing collection
    for edition in test_unhydrated_editions:
        crud.collection.add_item_to_collection(
            session,
            collection_orm_object=test_user_empty_collection,
            item=CollectionItemCreateIn(edition_isbn=edition.isbn),
            commit=False,
        )
    session.commit()
    yield test_user_empty_collection


@pytest.fixture()
def test_school_with_collection(
    client,
    session,
    test_school: School,
    test_unhydrated_editions,
    backend_service_account,
) -> School:
    collection, created = crud.collection.get_or_create(
        db=session,
        collection_data=CollectionCreateIn(
            name=f"Books at {test_school.name}",
            school_id=test_school.wriveted_identifier,
            info={"msg": "Created for test purposes"},
        ),
    )

    items = [
        CollectionItemUpdate(edition_isbn=e.isbn, action=CollectionUpdateType.ADD)
        for e in test_unhydrated_editions
    ]

    crud.collection.update(
        db=session, db_obj=collection, obj_in=CollectionAndItemsUpdateIn(items=items)
    )
    session.commit()

    collection: Collection = test_school.collection
    assert collection.book_count == len(test_unhydrated_editions)

    yield test_school

    reset_collection(
        session=session, collection=collection, account=backend_service_account
    )


@pytest.fixture()
def admin_of_test_school(session, test_school, test_schooladmin_account):
    test_schooladmin_account.school_id = test_school.id
    session.add(test_schooladmin_account)
    session.commit()
    yield test_schooladmin_account


@pytest.fixture()
def admin_of_test_school_token(admin_of_test_school):
    return create_user_access_token(admin_of_test_school)


@pytest.fixture()
def admin_of_test_school_headers(admin_of_test_school_token):
    return {"Authorization": f"bearer {admin_of_test_school_token}"}


@pytest.fixture()
def lms_service_account_for_test_school(session, test_school):
    print("Creating a LMS service account to carry out the rest of the test")
    sa = service_account_repository.create(
        db=session,
        obj_in=ServiceAccountCreateIn(
            **{
                "name": f"Integration Test Service Account - {test_school.id}",
                "type": "lms",
                "schools": [
                    {
                        "name": test_school.name,
                        "country_code": "ATA",
                        "official_identifier": test_school.id,
                        "wriveted_identifier": test_school.wriveted_identifier,
                    }
                ],
                "info": {"msg": "Created for test purposes"},
            }
        ),
    )

    yield sa

    try:
        service_account_repository.remove(db=session, id=sa.id)
        session.commit()
    except Exception:
        session.rollback()


@pytest.fixture()
def lms_service_account_token_for_school(settings, lms_service_account_for_test_school):
    access_token = create_access_token(
        subject=f"wriveted:service-account:{lms_service_account_for_test_school.id}",
        expires_delta=timedelta(
            minutes=settings.SERVICE_ACCOUNT_ACCESS_TOKEN_EXPIRE_MINUTES
        ),
    )
    return access_token


@pytest.fixture()
def lms_service_account_headers_for_school(lms_service_account_token_for_school):
    return {"Authorization": f"bearer {lms_service_account_token_for_school}"}


@pytest.fixture()
def test_public_user_hacker(session):
    hacker = crud.user.create(
        db=session,
        obj_in=UserCreateIn(
            name="NotAHacker", email=f"{random_lower_string(6)}@notahacker.com"
        ),
    )
    yield hacker
    try:
        session.delete(hacker)
        session.commit()
    except Exception:
        session.rollback()


@pytest.fixture()
def test_public_user_hacker_token(test_public_user_hacker):
    return create_user_access_token(test_public_user_hacker)


@pytest.fixture()
def test_public_user_hacker_headers(test_public_user_hacker_token):
    return {"Authorization": f"bearer {test_public_user_hacker_token}"}


@pytest.fixture()
def test_huey_attributes():
    return HueyAttributes(
        birthdate="2015-01-01 00:00:00",
        last_visited="2022-05-05 00:00:00",
        age=7,
        reading_ability=[ReadingAbilityKey.CAT_HAT],
        hues=[HueKeys.hue01_dark_suspense, HueKeys.hue03_dark_beautiful],
        goals=["Maintain a thoroughly-tested codebase"],
        genres=["Dark", "Realistic"],
        characters=["Robot", "Unicorn"],
    )


@pytest.fixture(autouse=True)
def clear_test_client_cookies(client):
    """Ensure test isolation by clearing TestClient cookies between tests.

    This fixes CSRF token conflicts when chat API tests run together.
    Each test gets a fresh cookie jar to prevent token interference.
    """
    # Clear all cookies before test runs
    client.cookies.clear()
    yield
    # Clear cookies after test completes for good measure
    client.cookies.clear()

import asyncio
from collections.abc import AsyncGenerator
from functools import lru_cache
from typing import Optional
from weakref import WeakKeyDictionary

from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from sqlalchemy import URL, create_engine
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker
from structlog import get_logger

from app.config import Settings, get_settings

logger = get_logger()


def database_connection(
    database_uri: str | URL,
    pool_size=10,
    max_overflow=10,
):
    # Ref: https://docs.sqlalchemy.org/en/14/core/pooling.html
    """
    Note Cloud SQL instance has a limited number of connections:
    Currently: 50 in non-prod and 100 in prod.

    The settings here need to be considered along with concurrency settings in
    Cloud Run - how many containers will be brought up, and how many requests
    can they each serve.
    """
    engine = create_engine(
        database_uri,
        pool_pre_ping=True,
        # Pool size is the maximum number of permanent connections to keep.
        # defaults to 5
        pool_size=pool_size,
        # Temporarily exceeds the set pool_size if no connections are available.
        # Default is 10
        max_overflow=max_overflow,
        # 'pool_recycle' is the maximum number of seconds a connection can persist.
        # Connections that live longer than the specified amount of time will be
        # reestablished on checkout.
        # https://docs.sqlalchemy.org/en/14/core/pooling.html#setting-pool-recycle
        pool_recycle=900,  # 15 minutes,
        # 'pool_timeout' is the maximum number of seconds to wait when retrieving a
        # new connection from the pool. After the specified amount of time, an
        # exception will be thrown.
        pool_timeout=120,
    )
    SessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=engine, future=True
    )
    return engine, SessionLocal


_async_session_makers: dict[
    tuple[str, int, int],
    WeakKeyDictionary[
        asyncio.AbstractEventLoop, tuple[AsyncEngine, async_sessionmaker]
    ],
] = {}


def _get_current_loop() -> asyncio.AbstractEventLoop:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.get_event_loop()


def _get_async_session_maker(
    database_uri: str, pool_size: int, max_overflow: int
) -> async_sessionmaker:
    cache_key = (database_uri, pool_size, max_overflow)
    loop_cache = _async_session_makers.setdefault(cache_key, WeakKeyDictionary())
    loop = _get_current_loop()
    cached = loop_cache.get(loop)
    if cached is not None:
        return cached[1]

    engine = create_async_engine(
        database_uri,
        pool_pre_ping=True,
        # Pool size is the maximum number of permanent connections to keep.
        # defaults to 5
        pool_size=pool_size,
        # Temporarily exceeds the set pool_size if no connections are available.
        # Default is 10
        max_overflow=max_overflow,
        # 'pool_recycle' is the maximum number of seconds a connection can persist.
        # Connections that live longer than the specified amount of time will be
        # reestablished on checkout.
        # https://docs.sqlalchemy.org/en/14/core/pooling.html#setting-pool-recycle
        pool_recycle=900,  # 15 minutes,
        # 'pool_timeout' is the maximum number of seconds to wait when retrieving a
        # new connection from the pool. After the specified amount of time, an
        # exception will be thrown.
        pool_timeout=120,
    )

    SQLAlchemyInstrumentor().instrument(
        engine=engine.sync_engine, enable_commenter=True, commenter_options={}
    )

    session_maker = async_sessionmaker(
        engine, autoflush=False, autocommit=False, expire_on_commit=False
    )
    loop_cache[loop] = (engine, session_maker)
    return session_maker


def reset_async_session_makers() -> None:
    for loop_cache in _async_session_makers.values():
        for engine, _ in loop_cache.values():
            try:
                engine.sync_engine.dispose()
            except Exception as exc:
                logger.warning(
                    "Failed to dispose async engine", error=str(exc), exc_info=True
                )
    _async_session_makers.clear()


def get_async_session_maker(settings: Optional[Settings] = None):
    if settings is None:
        settings = get_settings()

    async_uri = settings.SQLALCHEMY_ASYNC_URI
    if async_uri is None:
        raise ValueError("SQLALCHEMY_ASYNC_URI is required to build async sessions")
    async_uri_str = async_uri.render_as_string(hide_password=False)

    return _get_async_session_maker(
        async_uri_str,
        settings.DATABASE_POOL_SIZE,
        settings.DATABASE_MAX_OVERFLOW,
    )


@lru_cache(maxsize=None)
def _get_session_maker(
    database_uri: str, pool_size: int, max_overflow: int
) -> sessionmaker:
    engine, SessionLocal = database_connection(
        database_uri,
        pool_size=pool_size,
        max_overflow=max_overflow,
    )
    SQLAlchemyInstrumentor().instrument(
        engine=engine,
        enable_commenter=True,
    )
    return SessionLocal


def get_session_maker(settings: Optional[Settings] = None):
    if settings is None:
        settings = get_settings()

    database_uri = settings.SQLALCHEMY_DATABASE_URI
    if database_uri is None:
        raise ValueError("SQLALCHEMY_DATABASE_URI is required to build sessions")
    database_uri_str = database_uri.render_as_string(hide_password=False)

    return _get_session_maker(
        database_uri_str,
        settings.DATABASE_POOL_SIZE,
        settings.DATABASE_MAX_OVERFLOW,
    )


def get_session():
    logger.debug("Getting sync db session")
    session_factory = get_session_maker()
    with session_factory() as session:
        logger.debug("Got sync db session")
        yield session
        logger.debug("Cleaning up sync db session")


async def get_async_session() -> AsyncGenerator:
    logger.debug("Getting async db session")
    session_factory = get_async_session_maker()
    async with session_factory() as session:
        logger.debug("Got async db session")
        yield session
        logger.debug("Cleaning up async db session")

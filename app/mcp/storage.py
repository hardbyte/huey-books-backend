"""Durable, shared storage for the OAuth proxy's state (DCR client registrations,
transactions, refresh-token bookkeeping). Backed by our Postgres so it survives
instance recycling — the in-memory/disk defaults are per-instance and are lost on
every Cloud Run restart, which invalidates clients' tokens."""

from __future__ import annotations

from functools import lru_cache

from key_value.aio.stores.postgresql import PostgreSQLStore
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

from app.config import Settings, get_settings


@lru_cache(maxsize=1)
def get_mcp_storage():
    return build_oauth_client_storage(get_settings())


def build_oauth_client_storage(settings: Settings):
    kwargs: dict = {
        "database": settings.POSTGRESQL_DATABASE,
        "user": settings.POSTGRESQL_USER,
        "password": settings.POSTGRESQL_PASSWORD,
        "port": settings.POSTGRESQL_PORT,
        "table_name": "mcp_oauth_proxy_kv",
        "auto_create": False,
    }
    if settings.POSTGRESQL_DATABASE_SOCKET_PATH is not None:
        instance = (
            f"{settings.GCP_PROJECT_ID}:{settings.GCP_LOCATION}:"
            f"{settings.GCP_CLOUD_SQL_INSTANCE_ID}"
        )
        kwargs["host"] = f"{settings.POSTGRESQL_DATABASE_SOCKET_PATH}/{instance}"
    else:
        kwargs["host"] = settings.POSTGRESQL_SERVER

    store = PostgreSQLStore(**kwargs)
    return FernetEncryptionWrapper(
        store, source_material=settings.SECRET_KEY, salt="mcp-oauth-proxy"
    )

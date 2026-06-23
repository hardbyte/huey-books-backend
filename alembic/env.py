from __future__ import with_statement

import os
import sys
from logging.config import fileConfig

from alembic_utils.replaceable_entity import register_entities
from sqlalchemy import engine_from_config, pool

from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
fileConfig(config.config_file_name)

# Add project root directory to python path
sys.path.insert(
    0, os.path.abspath(os.path.join(__file__, os.path.pardir, os.path.pardir))
)

from app.db.base_class import Base  # noqa
from app.db.extensions import pg_trgm_ex, pgvector_ex
from app.db.functions import (
    cms_content_tsvector_update,
    notify_flow_event_function,
    public_encode_uri_component,
    refresh_recommendable_editions_function,
    refresh_search_view_v1_function,
    refresh_work_collection_frequency_view_function,
    update_collections_function,
    update_edition_title,
    update_edition_title_from_work,
)
from app.db.triggers import (
    cms_content_tsvector_trigger,
    conversation_sessions_notify_flow_event_trigger,
    editions_update_edition_title_trigger,
    update_collections_trigger,
    works_update_edition_title_from_work_trigger,
)
from app.db.views import (
    collection_frequency_view,
    recommendable_editions_view,
    search_view_v1,
)

register_entities(
    [
        # Extensions
        pgvector_ex,
        pg_trgm_ex,
        # Functions
        update_edition_title,
        update_edition_title_from_work,
        update_collections_function,
        refresh_search_view_v1_function,
        refresh_work_collection_frequency_view_function,
        refresh_recommendable_editions_function,
        public_encode_uri_component,
        cms_content_tsvector_update,
        notify_flow_event_function,
        # Views
        collection_frequency_view,
        search_view_v1,
        recommendable_editions_view,
        # Triggers
        editions_update_edition_title_trigger,
        works_update_edition_title_from_work_trigger,
        cms_content_tsvector_trigger,
        conversation_sessions_notify_flow_event_trigger,
        update_collections_trigger,
    ]
)

target_metadata = Base.metadata


# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def get_url():
    return os.getenv("SQLALCHEMY_DATABASE_URI", "sqlite:///db.sqlite")


def include_name(name, type_, parent_names) -> bool:
    if type_ == "grant_table":
        return False
    else:
        return True


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        # compare_type=True,
        include_name=include_name,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    configuration = config.get_section(config.config_ini_section)
    configuration["sqlalchemy.url"] = get_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    # connectable = create_engine(get_url())

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # compare_type=True,
            include_name=include_name,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

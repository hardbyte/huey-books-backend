"""Persist transactional search and recommendation refresh timestamps.

Revision ID: e7219c04fa68
Revises: a4b513cf892d
"""

import sqlalchemy as sa
from alembic_utils.pg_function import PGFunction

from alembic import op

revision = "e7219c04fa68"
down_revision = "a4b513cf892d"
branch_labels = None
depends_on = None

refresh_search_index_old = PGFunction(
    schema="public",
    signature="refresh_search_index()",
    definition="returns void LANGUAGE plpgsql SECURITY DEFINER\n      SET search_path = pg_catalog, pg_temp\n      AS $function$\n        BEGIN\n          REFRESH MATERIALIZED VIEW public.search_view_v1;\n        END;\n      $function$\n    ",
)

refresh_search_index_new = PGFunction(
    schema="public",
    signature="refresh_search_index()",
    definition="returns void LANGUAGE plpgsql SECURITY DEFINER\n      SET search_path = pg_catalog, pg_temp\n      AS $function$\n        DECLARE snapshot_at timestamptz := statement_timestamp();\n        BEGIN\n          REFRESH MATERIALIZED VIEW CONCURRENTLY public.search_view_v1;\n          INSERT INTO public.search_index_refreshes(index_name, source_snapshot_at, refreshed_at)\n          VALUES ('search_view_v1', snapshot_at, clock_timestamp())\n          ON CONFLICT (index_name) DO UPDATE\n          SET source_snapshot_at = EXCLUDED.source_snapshot_at,\n              refreshed_at = EXCLUDED.refreshed_at;\n          REFRESH MATERIALIZED VIEW CONCURRENTLY public.work_collection_frequency;\n          INSERT INTO public.search_index_refreshes(index_name, source_snapshot_at, refreshed_at)\n          VALUES ('work_collection_frequency', snapshot_at, clock_timestamp())\n          ON CONFLICT (index_name) DO UPDATE\n          SET source_snapshot_at = EXCLUDED.source_snapshot_at,\n              refreshed_at = EXCLUDED.refreshed_at;\n        END;\n      $function$\n    ",
)

refresh_recommendable_editions_function_old = PGFunction(
    schema="public",
    signature="refresh_recommendable_editions_function()",
    definition="returns void LANGUAGE plpgsql SECURITY DEFINER SET search_path = public\n      AS $function$\n        BEGIN\n        -- CONCURRENTLY avoids an ACCESS EXCLUSIVE lock so recommendation reads are\n        -- not blocked during the refresh; it requires the unique index on work_id.\n        REFRESH MATERIALIZED VIEW CONCURRENTLY public.recommendable_editions;\n        END;\n      $function$\n    ",
)

refresh_recommendable_editions_function_new = PGFunction(
    schema="public",
    signature="refresh_recommendable_editions_function()",
    definition="returns void LANGUAGE plpgsql SECURITY DEFINER\n      SET search_path = pg_catalog, pg_temp\n      AS $function$\n        DECLARE snapshot_at timestamptz := statement_timestamp();\n        BEGIN\n          REFRESH MATERIALIZED VIEW CONCURRENTLY public.recommendable_editions;\n          INSERT INTO public.search_index_refreshes(index_name, source_snapshot_at, refreshed_at)\n          VALUES ('recommendable_editions', snapshot_at, clock_timestamp())\n          ON CONFLICT (index_name) DO UPDATE\n          SET source_snapshot_at = EXCLUDED.source_snapshot_at,\n              refreshed_at = EXCLUDED.refreshed_at;\n        END;\n      $function$\n    ",
)


def upgrade():
    op.create_table(
        "search_index_refreshes",
        sa.Column("index_name", sa.String(64), nullable=False),
        sa.Column("source_snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("index_name", name="pk_search_index_refreshes"),
    )
    op.create_index(
        "uix_search_view_work_series",
        "search_view_v1",
        ["work_id", "series_id"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )
    op.replace_entity(refresh_search_index_new)
    op.replace_entity(refresh_recommendable_editions_function_new)


def downgrade():
    op.replace_entity(refresh_search_index_old)
    op.replace_entity(refresh_recommendable_editions_function_old)
    op.drop_index("uix_search_view_work_series", table_name="search_view_v1")
    op.drop_table("search_index_refreshes")

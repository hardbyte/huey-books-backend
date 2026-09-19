"""Allow concurrent catalogue refresh while preserving work-series rows.

Revision ID: f82ca640de17
Revises: e7219c04fa68
"""

from alembic_utils.pg_function import PGFunction

from alembic import op

revision = "f82ca640de17"
down_revision = "e7219c04fa68"
branch_labels = None
depends_on = None

previous_refresh = PGFunction(
    schema="public",
    signature="refresh_search_index()",
    definition="returns void LANGUAGE plpgsql SECURITY DEFINER\n      SET search_path = pg_catalog, pg_temp\n      AS $function$\n        DECLARE snapshot_at timestamptz := statement_timestamp();\n        BEGIN\n          REFRESH MATERIALIZED VIEW public.search_view_v1;\n          INSERT INTO public.search_index_refreshes(index_name, source_snapshot_at, refreshed_at)\n          VALUES ('search_view_v1', snapshot_at, clock_timestamp())\n          ON CONFLICT (index_name) DO UPDATE\n          SET source_snapshot_at = EXCLUDED.source_snapshot_at,\n              refreshed_at = EXCLUDED.refreshed_at;\n          REFRESH MATERIALIZED VIEW CONCURRENTLY public.work_collection_frequency;\n          INSERT INTO public.search_index_refreshes(index_name, source_snapshot_at, refreshed_at)\n          VALUES ('work_collection_frequency', snapshot_at, clock_timestamp())\n          ON CONFLICT (index_name) DO UPDATE\n          SET source_snapshot_at = EXCLUDED.source_snapshot_at,\n              refreshed_at = EXCLUDED.refreshed_at;\n        END;\n      $function$\n    ",
)

concurrent_refresh = PGFunction(
    schema="public",
    signature="refresh_search_index()",
    definition="returns void LANGUAGE plpgsql SECURITY DEFINER\n      SET search_path = pg_catalog, pg_temp\n      AS $function$\n        DECLARE snapshot_at timestamptz := statement_timestamp();\n        BEGIN\n          REFRESH MATERIALIZED VIEW CONCURRENTLY public.search_view_v1;\n          INSERT INTO public.search_index_refreshes(index_name, source_snapshot_at, refreshed_at)\n          VALUES ('search_view_v1', snapshot_at, clock_timestamp())\n          ON CONFLICT (index_name) DO UPDATE\n          SET source_snapshot_at = EXCLUDED.source_snapshot_at,\n              refreshed_at = EXCLUDED.refreshed_at;\n          REFRESH MATERIALIZED VIEW CONCURRENTLY public.work_collection_frequency;\n          INSERT INTO public.search_index_refreshes(index_name, source_snapshot_at, refreshed_at)\n          VALUES ('work_collection_frequency', snapshot_at, clock_timestamp())\n          ON CONFLICT (index_name) DO UPDATE\n          SET source_snapshot_at = EXCLUDED.source_snapshot_at,\n              refreshed_at = EXCLUDED.refreshed_at;\n        END;\n      $function$\n    ",
)


def upgrade():
    op.create_index(
        "uix_search_view_work_series",
        "search_view_v1",
        ["work_id", "series_id"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )
    op.replace_entity(concurrent_refresh)


def downgrade():
    op.replace_entity(previous_refresh)
    op.drop_index("uix_search_view_work_series", table_name="search_view_v1")

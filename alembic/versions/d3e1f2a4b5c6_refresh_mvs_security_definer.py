"""
Make MV-refresh functions SECURITY DEFINER

Only an MV's owner may REFRESH it; the MVs are owned by the migration role, not
the cloudrun runtime role, so the internal API's refresh calls (and the search /
collection-frequency triggers) failed with "permission denied for materialized
view". Running these fixed, fully-qualified refresh statements as SECURITY
DEFINER lets the runtime role trigger the refresh without owning the MV.

Revision ID: d3e1f2a4b5c6
Revises: f4b8c2d6e0a1
Create Date: 2026-09-02 00:00:00.000000
"""

from alembic_utils.pg_function import PGFunction

from alembic import op

revision = "d3e1f2a4b5c6"
down_revision = "f4b8c2d6e0a1"
branch_labels = None
depends_on = None


_new_search_view = PGFunction(
    schema="public",
    signature="refresh_search_view_v1_function()",
    definition="""returns trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = public
      AS $function$
        BEGIN
        REFRESH MATERIALIZED VIEW public.search_view_v1;
        RETURN NEW;
      END;
      $function$
    """,
)

_new_recommendable = PGFunction(
    schema="public",
    signature="refresh_recommendable_editions_function()",
    definition="""returns void LANGUAGE plpgsql SECURITY DEFINER SET search_path = public
      AS $function$
        BEGIN
        -- CONCURRENTLY avoids an ACCESS EXCLUSIVE lock so recommendation reads are
        -- not blocked during the refresh; it requires the unique index on work_id.
        REFRESH MATERIALIZED VIEW CONCURRENTLY public.recommendable_editions;
        END;
      $function$
    """,
)

_new_work_collection = PGFunction(
    schema="public",
    signature="refresh_work_collection_frequency_view_function()",
    definition="""returns trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = public
      AS $function$
        BEGIN
        REFRESH MATERIALIZED VIEW public.work_collection_frequency;
        RETURN NEW;
      END;
      $function$
    """,
)

_old_search_view = PGFunction(
    schema="public",
    signature="refresh_search_view_v1_function()",
    definition="""returns trigger LANGUAGE plpgsql
      AS $function$
        BEGIN
        REFRESH MATERIALIZED VIEW search_view_v1;
        RETURN NEW;
      END;
      $function$
    """,
)

_old_recommendable = PGFunction(
    schema="public",
    signature="refresh_recommendable_editions_function()",
    definition="""returns void LANGUAGE plpgsql
      AS $function$
        BEGIN
        -- CONCURRENTLY avoids an ACCESS EXCLUSIVE lock so recommendation reads are
        -- not blocked during the refresh; it requires the unique index on work_id.
        REFRESH MATERIALIZED VIEW CONCURRENTLY public.recommendable_editions;
        END;
      $function$
    """,
)

_old_work_collection = PGFunction(
    schema="public",
    signature="refresh_work_collection_frequency_view_function()",
    definition="""returns trigger LANGUAGE plpgsql
      AS $function$
        BEGIN
        REFRESH MATERIALIZED VIEW work_collection_frequency;
        RETURN NEW;
      END;
      $function$
    """,
)


def upgrade() -> None:
    op.replace_entity(_new_search_view)
    op.replace_entity(_new_recommendable)
    op.replace_entity(_new_work_collection)


def downgrade() -> None:
    op.replace_entity(_old_search_view)
    op.replace_entity(_old_recommendable)
    op.replace_entity(_old_work_collection)

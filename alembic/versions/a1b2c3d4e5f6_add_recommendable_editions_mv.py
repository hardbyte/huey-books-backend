"""Add recommendable_editions materialized view and refresh function

Pre-computes one row per recommendable work (latest labelset, cover edition,
hue/reading-ability key arrays) so the recommendation query can hit a small
indexed table instead of re-joining the full labelsets/editions/hues graph on
every request.

Revision ID: a1b2c3d4e5f6
Revises: c4e8a1f6d2b9
Create Date: 2026-06-23 00:00:00.000000

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = "c4e8a1f6d2b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from app.db.functions import refresh_recommendable_editions_function
    from app.db.views import recommendable_editions_view

    # Create the materialized view (WITH DATA so it's immediately queryable)
    op.create_entity(recommendable_editions_view)

    # Unique index on work_id — required for REFRESH MATERIALIZED VIEW CONCURRENTLY
    # (and backs the work_id lookups in the scored query).
    op.create_index(
        "uix_recommendable_editions_work_id",
        "recommendable_editions",
        ["work_id"],
        unique=True,
    )

    # GIN indexes for array overlap queries (hue_keys && :hues, reading_ability_keys && :ras)
    op.create_index(
        "ix_recommendable_editions_hue_keys",
        "recommendable_editions",
        ["hue_keys"],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_recommendable_editions_reading_ability_keys",
        "recommendable_editions",
        ["reading_ability_keys"],
        postgresql_using="gin",
    )

    # Btree index covering the hard-filter columns used on every recommendation query
    op.create_index(
        "ix_recommendable_editions_status_ages",
        "recommendable_editions",
        ["recommend_status", "min_age", "max_age"],
    )

    # The refresh function (REFRESH MATERIALIZED VIEW CONCURRENTLY)
    op.create_entity(refresh_recommendable_editions_function)


def downgrade() -> None:
    from app.db.functions import refresh_recommendable_editions_function
    from app.db.views import recommendable_editions_view

    op.drop_entity(refresh_recommendable_editions_function)

    op.drop_index(
        "ix_recommendable_editions_status_ages", table_name="recommendable_editions"
    )
    op.drop_index(
        "ix_recommendable_editions_reading_ability_keys",
        table_name="recommendable_editions",
    )
    op.drop_index(
        "ix_recommendable_editions_hue_keys", table_name="recommendable_editions"
    )
    op.drop_index(
        "uix_recommendable_editions_work_id", table_name="recommendable_editions"
    )

    op.drop_entity(recommendable_editions_view)

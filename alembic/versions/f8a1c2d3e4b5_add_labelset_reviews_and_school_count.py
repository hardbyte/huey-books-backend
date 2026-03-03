"""Add labelset_reviews table and school_count to collection frequency view

Revision ID: f8a1c2d3e4b5
Revises: 3252097e86db
Create Date: 2026-03-01 12:00:00.000000

"""

import sqlalchemy as sa
from alembic_utils.pg_materialized_view import PGMaterializedView
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "f8a1c2d3e4b5"
down_revision = "3252097e86db"
branch_labels = None
depends_on = None

OLD_VIEW_DEFINITION = """
SELECT
    e.work_id,
    SUM(ci.copies_total) AS collection_frequency
FROM
    public.editions e
JOIN
    public.collection_items ci ON ci.edition_isbn = e.isbn
GROUP BY
    e.work_id
"""

NEW_VIEW_DEFINITION = """
SELECT
    e.work_id,
    SUM(ci.copies_total) AS collection_frequency,
    COUNT(DISTINCT c.school_id)
        FILTER (WHERE c.school_id IS NOT NULL) AS school_count
FROM
    public.editions e
JOIN
    public.collection_items ci ON ci.edition_isbn = e.isbn
JOIN
    public.collections c ON c.id = ci.collection_id
GROUP BY
    e.work_id
"""


def upgrade():
    # Create the labelset_reviews table
    op.create_table(
        "labelset_reviews",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("labelset_id", sa.Integer(), nullable=False),
        sa.Column("reviewer_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("hue_primary_key", sa.String(50), nullable=True),
        sa.Column("hue_secondary_key", sa.String(50), nullable=True),
        sa.Column("hue_tertiary_key", sa.String(50), nullable=True),
        sa.Column("min_age", sa.Integer(), nullable=True),
        sa.Column("max_age", sa.Integer(), nullable=True),
        sa.Column("reading_ability_key", sa.String(), nullable=True),
        sa.Column(
            "recommend_status",
            postgresql.ENUM(
                "GOOD",
                "BAD_BORING",
                "BAD_REFERENCE",
                "BAD_CONTROVERSIAL",
                "BAD_LOW_QUALITY",
                name="recommendstatus",
                create_type=False,
            ),
            nullable=True,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("confirmed_existing", sa.Boolean(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["labelset_id"],
            ["labelsets.id"],
            name="fk_labelset_review_labelset",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_user_id"],
            ["users.id"],
            name="fk_labelset_review_user",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["hue_primary_key"],
            ["hues.key"],
            name="fk_labelset_review_hue_primary",
        ),
        sa.ForeignKeyConstraint(
            ["hue_secondary_key"],
            ["hues.key"],
            name="fk_labelset_review_hue_secondary",
        ),
        sa.ForeignKeyConstraint(
            ["hue_tertiary_key"],
            ["hues.key"],
            name="fk_labelset_review_hue_tertiary",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_labelset_reviews")),
        sa.UniqueConstraint(
            "labelset_id",
            "reviewer_user_id",
            name="uq_labelset_reviews_labelset_reviewer",
        ),
    )
    op.create_index(
        op.f("ix_labelset_reviews_labelset_id"),
        "labelset_reviews",
        ["labelset_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_labelset_reviews_reviewer_user_id"),
        "labelset_reviews",
        ["reviewer_user_id"],
        unique=False,
    )

    # Update the materialized view to include school_count.
    # Materialized views can't be altered in-place — drop and recreate.
    old_view = PGMaterializedView(
        schema="public",
        signature="work_collection_frequency",
        definition=OLD_VIEW_DEFINITION,
        with_data=True,
    )
    new_view = PGMaterializedView(
        schema="public",
        signature="work_collection_frequency",
        definition=NEW_VIEW_DEFINITION,
        with_data=True,
    )
    op.drop_entity(old_view)
    op.create_entity(new_view)
    op.create_index(None, "work_collection_frequency", ["work_id"], unique=True)


def downgrade():
    # Revert the materialized view
    new_view = PGMaterializedView(
        schema="public",
        signature="work_collection_frequency",
        definition=NEW_VIEW_DEFINITION,
        with_data=True,
    )
    old_view = PGMaterializedView(
        schema="public",
        signature="work_collection_frequency",
        definition=OLD_VIEW_DEFINITION,
        with_data=True,
    )
    op.drop_entity(new_view)
    op.create_entity(old_view)
    op.create_index(None, "work_collection_frequency", ["work_id"], unique=True)

    # Drop the labelset_reviews table
    op.drop_index(
        op.f("ix_labelset_reviews_reviewer_user_id"),
        table_name="labelset_reviews",
    )
    op.drop_index(
        op.f("ix_labelset_reviews_labelset_id"),
        table_name="labelset_reviews",
    )
    op.drop_table("labelset_reviews")

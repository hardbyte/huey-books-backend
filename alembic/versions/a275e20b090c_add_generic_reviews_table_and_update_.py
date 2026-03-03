"""Add generic reviews table and update collection frequency view

Revision ID: a275e20b090c
Revises: 3252097e86db
Create Date: 2026-03-03 22:03:08.056898

"""

import sqlalchemy as sa
from alembic_utils.pg_materialized_view import PGMaterializedView
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "a275e20b090c"
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
    # Create the generic reviews table
    op.create_table(
        "reviews",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "reviewable_type",
            sa.Enum(
                "LABELSET", "CMS_CONTENT", "FLOW_DEFINITION", name="reviewabletype"
            ),
            nullable=False,
        ),
        sa.Column("reviewable_id", sa.String(), nullable=False),
        sa.Column("reviewer_user_id", sa.UUID(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("assessment", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_user_id"],
            ["users.id"],
            name="fk_review_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reviews")),
        sa.UniqueConstraint(
            "reviewable_type",
            "reviewable_id",
            "reviewer_user_id",
            name="uq_reviews_type_entity_reviewer",
        ),
    )
    op.create_index(
        "ix_reviews_type_entity",
        "reviews",
        ["reviewable_type", "reviewable_id"],
        unique=False,
    )

    # Update materialized view to include school_count
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

    op.drop_index("ix_reviews_type_entity", table_name="reviews")
    op.drop_table("reviews")

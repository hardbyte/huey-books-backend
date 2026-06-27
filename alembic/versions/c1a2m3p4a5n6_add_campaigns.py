"""Add campaigns table (per-school/per-region chatflow segments)

Targeting rule + payload bundle (flow / theme / booklist) resolved per
school / region / season. See docs/design-chatflow-segments.md.

Revision ID: c1a2m3p4a5n6
Revises: a1b2c3d4e5f6
Create Date: 2026-06-27 00:00:00.000000

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "c1a2m3p4a5n6"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # create_table auto-creates this enum type from the column definition below.
    bias_mode = sa.Enum("boost", "filter", name="enum_campaign_bias_mode")

    # Reuse the existing visibility enum type shared by CMS content/flows.
    visibility = postgresql.ENUM(
        "private",
        "school",
        "public",
        "wriveted",
        name="enum_cms_content_visibility",
        create_type=False,
    )

    op.create_table(
        "campaigns",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("slug", sa.String(length=200), nullable=True),
        sa.Column("flow_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("theme_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("booklist_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("bias_mode", bias_mode, server_default="boost", nullable=False),
        sa.Column("country_codes", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("region_states", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("school_ids", postgresql.ARRAY(sa.Integer()), nullable=True),
        sa.Column("min_age", sa.Integer(), nullable=True),
        sa.Column("max_age", sa.Integer(), nullable=True),
        sa.Column("targeting_cel", sa.Text(), nullable=True),
        sa.Column("active_from", sa.DateTime(), nullable=True),
        sa.Column("active_until", sa.DateTime(), nullable=True),
        sa.Column("priority", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("info", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("school_id", sa.Integer(), nullable=True),
        sa.Column(
            "visibility",
            visibility,
            server_default=sa.text("'wriveted'"),
            nullable=False,
        ),
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
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["flow_id"],
            ["flow_definitions.id"],
            name="fk_campaign_flow",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["theme_id"],
            ["chat_themes.id"],
            name="fk_campaign_theme",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["booklist_id"],
            ["book_lists.id"],
            name="fk_campaign_booklist",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_campaign_created_by",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["school_id"],
            ["schools.id"],
            name="fk_campaign_school",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_campaigns_name", "campaigns", ["name"])
    op.create_index("ix_campaigns_slug", "campaigns", ["slug"], unique=True)
    op.create_index("ix_campaigns_visibility", "campaigns", ["visibility"])
    op.create_index("ix_campaigns_is_active", "campaigns", ["is_active"])


def downgrade() -> None:
    op.drop_index("ix_campaigns_is_active", table_name="campaigns")
    op.drop_index("ix_campaigns_visibility", table_name="campaigns")
    op.drop_index("ix_campaigns_slug", table_name="campaigns")
    op.drop_index("ix_campaigns_name", table_name="campaigns")
    op.drop_table("campaigns")
    # Drop only the enum we created; enum_cms_content_visibility is shared.
    sa.Enum(name="enum_campaign_bias_mode").drop(op.get_bind(), checkfirst=True)

"""Add library chat settings.

Revision ID: 93a402be781c
Revises: 82f391ad670b
"""

import sqlalchemy as sa

from alembic import op

revision = "93a402be781c"
down_revision = "82f391ad670b"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "library_chat_settings",
        sa.Column("library_uuid", sa.UUID(), nullable=False),
        sa.Column("catalogue_policy", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("jokes_enabled", sa.Boolean(), nullable=False),
        sa.Column("spelling_enabled", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "catalogue_policy IN ('library_only', 'prefer_library')",
            name=op.f("ck_library_chat_settings_valid_catalogue_policy"),
        ),
        sa.CheckConstraint(
            "revision > 0", name=op.f("ck_library_chat_settings_positive_revision")
        ),
        sa.ForeignKeyConstraint(
            ["library_uuid"],
            ["schools.wriveted_identifier"],
            name=op.f("fk_library_chat_settings_library_uuid_schools"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("library_uuid", name=op.f("pk_library_chat_settings")),
    )


def downgrade():
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM library_chat_settings)")
    ):
        raise RuntimeError("Cannot discard saved library chat settings")
    op.drop_table("library_chat_settings")

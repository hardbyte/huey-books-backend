"""Add selected-library chat attribution.

Revision ID: a4b513cf892d
Revises: 93a402be781c
"""

import sqlalchemy as sa

from alembic import op

revision = "a4b513cf892d"
down_revision = "93a402be781c"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "conversation_sessions", sa.Column("library_id", sa.UUID(), nullable=True)
    )
    op.create_foreign_key(
        "fk_session_library",
        "conversation_sessions",
        "schools",
        ["library_id"],
        ["wriveted_identifier"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_conversation_sessions_library_id_started",
        "conversation_sessions",
        ["library_id", "started_at"],
        unique=False,
    )


def downgrade():
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM conversation_sessions WHERE library_id IS NOT NULL)"
        )
    ):
        raise RuntimeError("Cannot discard recorded library attribution")
    op.drop_index(
        "ix_conversation_sessions_library_id_started",
        table_name="conversation_sessions",
    )
    op.drop_constraint(
        "fk_session_library", "conversation_sessions", type_="foreignkey"
    )
    op.drop_column("conversation_sessions", "library_id")

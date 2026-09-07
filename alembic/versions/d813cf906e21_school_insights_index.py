"""Index school-scoped conversation cohorts.

Revision ID: d813cf906e21
Revises: a1c2e3f40014
"""

import sqlalchemy as sa

from alembic import op

revision = "d813cf906e21"
down_revision = "a1c2e3f40014"
branch_labels = None
depends_on = None


def upgrade():
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_conversation_sessions_school_started",
            "conversation_sessions",
            [sa.text("(state #>> '{context,school_wriveted_id}')"), "started_at"],
            postgresql_concurrently=True,
            postgresql_where=sa.text(
                "length(state #>> '{context,school_wriveted_id}') = 36"
            ),
        )


def downgrade():
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_conversation_sessions_school_started",
            "conversation_sessions",
            postgresql_concurrently=True,
        )

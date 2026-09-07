"""Add typed school attribution to conversation sessions.

Revision ID: e927ad418b62
Revises: a1c2e3f40014
"""

import sqlalchemy as sa

from alembic import op

revision = "e927ad418b62"
down_revision = "a1c2e3f40014"
branch_labels = None
depends_on = None


def upgrade():
    # Release each DDL lock before the batched data update.
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '5s'")
        try:
            op.execute(
                "ALTER TABLE conversation_sessions ADD COLUMN IF NOT EXISTS school_id uuid"
            )
            constraints = sa.inspect(op.get_bind()).get_foreign_keys(
                "conversation_sessions"
            )
            if not any(
                constraint["name"] == "fk_session_school" for constraint in constraints
            ):
                op.execute("""ALTER TABLE conversation_sessions ADD CONSTRAINT fk_session_school
                    FOREIGN KEY (school_id) REFERENCES schools(wriveted_identifier)
                    ON DELETE SET NULL NOT VALID""")
            while True:
                result = op.get_bind().execute(
                    sa.text("""
                    WITH batch AS (
                        SELECT session.id, school.wriveted_identifier AS school_id
                        FROM conversation_sessions session JOIN schools school
                          ON lower(session.state #>> '{context,school_wriveted_id}')
                            = school.wriveted_identifier::text
                        WHERE session.school_id IS NULL
                          AND session.info ->> 'school_attribution_version' IS NULL
                        ORDER BY session.id LIMIT 1000
                    )
                    UPDATE conversation_sessions session SET school_id = batch.school_id
                    FROM batch WHERE session.id = batch.id AND session.school_id IS NULL
                """)
                )
                if result.rowcount == 0:
                    break
            op.execute(
                "ALTER TABLE conversation_sessions VALIDATE CONSTRAINT fk_session_school"
            )
            # Failed concurrent builds can leave an invalid index; rebuild on retry.
            op.execute(
                "DROP INDEX CONCURRENTLY IF EXISTS ix_conversation_sessions_school_id_started"
            )
            op.create_index(
                "ix_conversation_sessions_school_id_started",
                "conversation_sessions",
                ["school_id", "started_at"],
                postgresql_concurrently=True,
            )
            op.execute(
                "DROP INDEX CONCURRENTLY IF EXISTS ix_conversation_sessions_school_started"
            )
        finally:
            op.execute("RESET lock_timeout")


def downgrade():
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_conversation_sessions_school_id_started",
            "conversation_sessions",
            postgresql_concurrently=True,
        )
    op.drop_constraint("fk_session_school", "conversation_sessions", type_="foreignkey")
    op.drop_column("conversation_sessions", "school_id")

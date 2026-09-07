"""Cover catalogue ISBN to work lookups.

Revision ID: f038be529c73
Revises: e927ad418b62
"""

import sqlalchemy as sa

from alembic import op

revision = "f038be529c73"
down_revision = "e927ad418b62"
branch_labels = None
depends_on = None


def upgrade():
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '5s'")
        try:
            existing = (
                op.get_bind()
                .execute(
                    sa.text("""
                    SELECT indisvalid,
                        indrelid = 'editions'::regclass
                        AND indnkeyatts = 1 AND indnatts = 2
                        AND indpred IS NULL AND indexprs IS NULL
                        AND pg_get_indexdef(indexrelid, 1, true) = 'isbn'
                        AND pg_get_indexdef(indexrelid, 2, true) = 'work_id'
                        AS expected_definition
                    FROM pg_index
                    WHERE indexrelid = to_regclass('ix_editions_isbn_work_id')
                """)
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None and not existing["indisvalid"]:
                raise RuntimeError(
                    "Invalid ix_editions_isbn_work_id: remove the failed index build before retrying"
                )
            if existing is not None and not existing["expected_definition"]:
                raise RuntimeError("Unexpected definition for ix_editions_isbn_work_id")
            if existing is None:
                op.create_index(
                    "ix_editions_isbn_work_id",
                    "editions",
                    ["isbn"],
                    postgresql_include=["work_id"],
                    postgresql_concurrently=True,
                )
        finally:
            op.execute("RESET lock_timeout")


def downgrade():
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '5s'")
        try:
            op.drop_index(
                "ix_editions_isbn_work_id", "editions", postgresql_concurrently=True
            )
        finally:
            op.execute("RESET lock_timeout")

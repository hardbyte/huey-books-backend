"""Index latest activity by holding and reader.

Revision ID: e350ba74bc12
Revises: d24fa963ab01
"""

from alembic import op

revision = "e350ba74bc12"
down_revision = "d24fa963ab01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS public.ix_collection_activity_latest"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY ix_collection_activity_latest ON public.collection_item_activity_log (collection_item_id, reader_id, timestamp DESC, id DESC)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY public.ix_collection_activity_latest")

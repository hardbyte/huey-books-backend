"""Enable pg_textsearch for BM25 experiments.

Revision ID: c13ed852fa90
Revises: f82ca640de17
"""

from alembic_utils.pg_extension import PGExtension

from alembic import op

revision = "c13ed852fa90"
down_revision = "f82ca640de17"
branch_labels = None
depends_on = None

pg_textsearch = PGExtension(schema="public", signature="pg_textsearch")


def upgrade():
    op.create_entity(pg_textsearch)


def downgrade():
    op.execute("DROP EXTENSION pg_textsearch RESTRICT")

"""Allow educators without a home school.

Revision ID: 82f391ad670b
Revises: 0a6db7f4e561
"""

import sqlalchemy as sa

from alembic import op

revision = "82f391ad670b"
down_revision = "0a6db7f4e561"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("educators", "school_id", existing_type=sa.INTEGER(), nullable=True)


def downgrade():
    op.alter_column(
        "educators", "school_id", existing_type=sa.INTEGER(), nullable=False
    )

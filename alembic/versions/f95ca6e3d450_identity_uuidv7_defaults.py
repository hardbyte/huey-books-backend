"""Use UUIDv7 defaults for new organisational identities.

Revision ID: f95ca6e3d450
Revises: e84b95d2c34f
"""

import sqlalchemy as sa

from alembic import op

revision = "f95ca6e3d450"
down_revision = "e84b95d2c34f"
branch_labels = None
depends_on = None


def upgrade():
    for table_name in ("organisations", "libraries", "education_units"):
        op.alter_column(table_name, "id", server_default=sa.text("uuidv7()"))


def downgrade():
    for table_name in ("organisations", "libraries", "education_units"):
        op.alter_column(table_name, "id", server_default=None)

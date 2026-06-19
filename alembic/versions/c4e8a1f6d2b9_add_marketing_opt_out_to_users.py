"""Add marketing_opt_out flag to users

Lets recipients unsubscribe from staff broadcasts/announcements (the educator
email channel). Existing educators remain reachable for service updates by
default; the flag is set when they click the unsubscribe link.

Revision ID: c4e8a1f6d2b9
Revises: b7e3f1a9c2d5
Create Date: 2026-06-19 02:00:00.000000

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "c4e8a1f6d2b9"
down_revision = "b7e3f1a9c2d5"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users",
        sa.Column(
            "marketing_opt_out",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade():
    op.drop_column("users", "marketing_opt_out")

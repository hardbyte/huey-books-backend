"""Add model-independent AI-assisted label origin.

Revision ID: a1c2e3f40013
Revises: a1c2e3f40012
"""

from alembic import op

revision = "a1c2e3f40013"
down_revision = "a1c2e3f40012"
branch_labels = None
depends_on = None


def upgrade():
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE labelorigin ADD VALUE IF NOT EXISTS 'AI_ASSISTED'")


def downgrade():
    # PostgreSQL cannot remove an enum value without rebuilding dependent columns.
    # Keep the additive value so existing provenance remains readable on upgrade.
    pass

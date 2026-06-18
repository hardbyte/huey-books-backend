"""Add EDUCATOR value to labelorigin enum

Lets teacher reviews be promoted into canonical labelsets with an authority
weight above AI labels but below Wriveted staff (HUMAN).

Revision ID: d2a7c9b3e1f4
Revises: a275e20b090c
Create Date: 2026-06-18 22:30:00.000000

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "d2a7c9b3e1f4"
down_revision = "a275e20b090c"
branch_labels = None
depends_on = None


def upgrade():
    # ADD VALUE IF NOT EXISTS is idempotent and, on PostgreSQL 12+, is allowed
    # inside a transaction as long as the new value is not used in the same
    # transaction (it is only used at runtime, in later transactions).
    op.execute("ALTER TYPE labelorigin ADD VALUE IF NOT EXISTS 'EDUCATOR'")


def downgrade():
    # PostgreSQL does not support removing a value from an enum type without
    # recreating it; leaving the value in place is harmless.
    pass

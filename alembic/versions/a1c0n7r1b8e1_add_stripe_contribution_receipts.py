"""add stripe_contribution_receipts idempotency table

Revision ID: a1c0n7r1b8e1
Revises: c1a2m3p4a5n6
Create Date: 2026-07-16 00:00:00.000000

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "a1c0n7r1b8e1"
down_revision = "c1a2m3p4a5n6"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "stripe_contribution_receipts",
        sa.Column("checkout_session_id", sa.String(), nullable=False),
        sa.Column("school_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("amount_total", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(), nullable=True),
        sa.Column("crediting", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        # The primary key on the checkout session id is the idempotency guard:
        # the webhook claims a session with INSERT ... ON CONFLICT DO NOTHING.
        sa.PrimaryKeyConstraint(
            "checkout_session_id", name="pk_stripe_contribution_receipts"
        ),
    )
    op.create_index(
        "ix_stripe_contribution_receipts_school_id",
        "stripe_contribution_receipts",
        ["school_id"],
    )


def downgrade():
    op.drop_index(
        "ix_stripe_contribution_receipts_school_id",
        table_name="stripe_contribution_receipts",
    )
    op.drop_table("stripe_contribution_receipts")

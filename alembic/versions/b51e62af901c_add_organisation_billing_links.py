"""Add explicit organisation billing sponsorship.

Revision ID: b51e62af901c
Revises: a149cf630d84
"""

import sqlalchemy as sa

from alembic import op

revision = "b51e62af901c"
down_revision = "a149cf630d84"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "organisation_billing_links",
        sa.Column(
            "organisation_id",
            sa.UUID(),
            sa.ForeignKey("organisations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "library_uuid",
            sa.UUID(),
            sa.ForeignKey("schools.wriveted_identifier", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "linked_by",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_organisation_billing_links_linked_by",
        "organisation_billing_links",
        ["linked_by"],
    )


def downgrade():
    if (
        op.get_bind()
        .execute(sa.text("SELECT EXISTS (SELECT 1 FROM organisation_billing_links)"))
        .scalar()
    ):
        raise RuntimeError(
            "Remove organisation billing links explicitly before downgrading"
        )
    op.drop_table("organisation_billing_links")

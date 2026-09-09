"""Associate subscriptions directly with organisations.

Revision ID: c62f73b0a12d
Revises: b51e62af901c
"""

import sqlalchemy as sa

from alembic import op

revision = "c62f73b0a12d"
down_revision = "b51e62af901c"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "organisation_subscriptions",
        sa.Column(
            "subscription_id",
            sa.String(),
            sa.ForeignKey("subscriptions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "organisation_id",
            sa.UUID(),
            sa.ForeignKey("organisations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "linked_by", sa.UUID(), sa.ForeignKey("users.id", ondelete="SET NULL")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_organisation_subscriptions_organisation_id",
        "organisation_subscriptions",
        ["organisation_id"],
    )
    op.create_index(
        "ix_organisation_subscriptions_linked_by",
        "organisation_subscriptions",
        ["linked_by"],
    )
    op.execute("""
        INSERT INTO organisation_subscriptions (subscription_id, organisation_id, linked_by, created_at)
        SELECT subscription.id, link.organisation_id, link.linked_by, link.created_at
        FROM organisation_billing_links link
        JOIN schools library ON library.wriveted_identifier = link.library_uuid
            AND library.organisation_id = link.organisation_id
        JOIN subscriptions subscription ON subscription.school_id = link.library_uuid
        WHERE subscription.type IN ('SCHOOL', 'LIBRARY') AND subscription.parent_id IS NULL
    """)


def downgrade():
    if (
        op.get_bind()
        .execute(sa.text("SELECT EXISTS (SELECT 1 FROM organisation_subscriptions)"))
        .scalar()
    ):
        raise RuntimeError(
            "Reconcile organisation subscription ownership explicitly before downgrading"
        )
    op.drop_table("organisation_subscriptions")

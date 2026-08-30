"""Add durable school billing state.

Revision ID: f4b8c2d6e0a1
Revises: e3a7c9d1f2b4
Create Date: 2026-08-31
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "f4b8c2d6e0a1"
down_revision = "e3a7c9d1f2b4"
branch_labels = None
depends_on = None


billing_method = postgresql.ENUM(
    "CARD", "INVOICE", name="enum_school_billing_method", create_type=False
)
billing_attempt_status = postgresql.ENUM(
    "CREATING",
    "CHECKOUT_OPEN",
    "INVOICE_OPEN",
    "PAID",
    "EXPIRED",
    "FAILED",
    "VOIDED",
    "UNCOLLECTIBLE",
    "CANCELLED",
    name="enum_school_billing_attempt_status",
    create_type=False,
)


def upgrade():
    billing_method.create(op.get_bind(), checkfirst=True)
    billing_attempt_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "school_billing_accounts",
        sa.Column("school_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stripe_customer_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["school_id"],
            ["schools.wriveted_identifier"],
            name="fk_school_billing_account_school",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("school_id"),
        sa.UniqueConstraint("stripe_customer_id"),
    )
    op.create_table(
        "school_billing_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("school_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("method", billing_method, nullable=False),
        sa.Column("status", billing_attempt_status, nullable=False),
        sa.Column("client_idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("configured_price_id", sa.String(), nullable=False),
        sa.Column("stripe_customer_id", sa.String(), nullable=True),
        sa.Column("stripe_checkout_session_id", sa.String(), nullable=True),
        sa.Column("stripe_subscription_id", sa.String(), nullable=True),
        sa.Column("stripe_invoice_id", sa.String(), nullable=True),
        sa.Column("checkout_url", sa.String(), nullable=True),
        sa.Column("hosted_invoice_url", sa.String(), nullable=True),
        sa.Column("billing_email", sa.String(length=320), nullable=True),
        sa.Column("billing_name", sa.String(length=200), nullable=True),
        sa.Column("purchase_order_number", sa.String(length=100), nullable=True),
        sa.Column("invoice_days_until_due", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("last_stripe_event_created_at", sa.DateTime(), nullable=True),
        sa.Column("failure_reason", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["school_id"],
            ["schools.wriveted_identifier"],
            name="fk_school_billing_attempt_school",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "school_id",
            "client_idempotency_key",
            name="uq_school_billing_attempt_school_idempotency_key",
        ),
        sa.UniqueConstraint("stripe_checkout_session_id"),
    )
    op.create_index(
        "ix_school_billing_attempts_stripe_invoice_id",
        "school_billing_attempts",
        ["stripe_invoice_id"],
    )
    op.create_index(
        "ix_school_billing_attempts_stripe_subscription_id",
        "school_billing_attempts",
        ["stripe_subscription_id"],
    )
    op.create_index(
        "ix_school_billing_attempts_school_created_at",
        "school_billing_attempts",
        ["school_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_school_billing_attempts_open_expiry",
        "school_billing_attempts",
        ["expires_at", "school_id"],
        postgresql_where=sa.text(
            "status IN ('CREATING', 'CHECKOUT_OPEN', 'INVOICE_OPEN')"
        ),
    )
    op.create_index(
        "uq_school_billing_attempt_one_open_collectible",
        "school_billing_attempts",
        ["school_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('CREATING', 'CHECKOUT_OPEN', 'INVOICE_OPEN')"
        ),
    )
    op.create_table(
        "stripe_event_receipts",
        sa.Column("event_id", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("event_created_at", sa.DateTime(), nullable=True),
        sa.Column("api_version", sa.String(), nullable=True),
        sa.Column("processed_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
    )

    op.add_column(
        "subscriptions", sa.Column("stripe_status", sa.String(), nullable=True)
    )
    op.add_column(
        "subscriptions", sa.Column("collection_method", sa.String(), nullable=True)
    )
    op.add_column("subscriptions", sa.Column("paid_at", sa.DateTime(), nullable=True))
    op.add_column(
        "subscriptions",
        sa.Column("last_stripe_event_created_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_subscriptions_active_expiration",
        "subscriptions",
        ["expiration", "school_id"],
        postgresql_where=sa.text("is_active"),
    )

    op.execute(
        """
        UPDATE subscriptions
        SET stripe_status = CASE WHEN is_active THEN 'active' ELSE 'canceled' END,
            collection_method = 'charge_automatically'
        WHERE stripe_customer_id <> ''
        """
    )


def downgrade():
    op.drop_index(
        "ix_subscriptions_active_expiration",
        table_name="subscriptions",
    )
    op.drop_column("subscriptions", "last_stripe_event_created_at")
    op.drop_column("subscriptions", "paid_at")
    op.drop_column("subscriptions", "collection_method")
    op.drop_column("subscriptions", "stripe_status")
    op.drop_table("stripe_event_receipts")
    op.drop_index(
        "ix_school_billing_attempts_open_expiry",
        table_name="school_billing_attempts",
    )
    op.drop_index(
        "ix_school_billing_attempts_school_created_at",
        table_name="school_billing_attempts",
    )
    op.drop_index(
        "uq_school_billing_attempt_one_open_collectible",
        table_name="school_billing_attempts",
    )
    op.drop_index(
        "ix_school_billing_attempts_stripe_subscription_id",
        table_name="school_billing_attempts",
    )
    op.drop_index(
        "ix_school_billing_attempts_stripe_invoice_id",
        table_name="school_billing_attempts",
    )
    op.drop_table("school_billing_attempts")
    op.drop_table("school_billing_accounts")
    billing_attempt_status.drop(op.get_bind(), checkfirst=True)
    billing_method.drop(op.get_bind(), checkfirst=True)

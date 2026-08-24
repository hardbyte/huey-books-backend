"""Add school_invitations table

Revision ID: c9e1f2a3b4d5
Revises: a1c0n7r1b8e1
Create Date: 2026-08-24

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "c9e1f2a3b4d5"
down_revision = "a1c0n7r1b8e1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "school_invitations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("token", sa.String(), nullable=False),
        sa.Column("inviter_school_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("inviter_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("invited_school_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("invited_school_name", sa.String(length=300), nullable=False),
        sa.Column("country_code", sa.String(length=3), nullable=True),
        sa.Column("invited_contact_email", sa.String(), nullable=False),
        sa.Column("invited_contact_name", sa.String(length=200), nullable=True),
        sa.Column("grant_days", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "SENT",
                "ACCEPTED",
                "EXPIRED",
                "REVOKED",
                name="enum_school_invitation_status",
            ),
            nullable=False,
        ),
        sa.Column("redeemed_subscription_id", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("accepted_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("info", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(
            ["inviter_school_id"],
            ["schools.wriveted_identifier"],
            name="fk_invitation_inviter_school",
        ),
        sa.ForeignKeyConstraint(
            ["inviter_user_id"],
            ["users.id"],
            name="fk_invitation_inviter_user",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["invited_school_id"],
            ["schools.wriveted_identifier"],
            name="fk_invitation_invited_school",
        ),
        sa.ForeignKeyConstraint(
            ["country_code"], ["countries.id"], name="fk_invitation_country"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_school_invitations_token", "school_invitations", ["token"], unique=True
    )
    op.create_index(
        "ix_school_invitations_inviter_school_id",
        "school_invitations",
        ["inviter_school_id"],
    )
    op.create_index(
        "ix_school_invitations_invited_school_id",
        "school_invitations",
        ["invited_school_id"],
    )
    op.create_index(
        "ix_school_invitations_invited_contact_email",
        "school_invitations",
        ["invited_contact_email"],
    )
    op.create_index("ix_school_invitations_status", "school_invitations", ["status"])


def downgrade():
    op.drop_index("ix_school_invitations_status", table_name="school_invitations")
    op.drop_index(
        "ix_school_invitations_invited_contact_email", table_name="school_invitations"
    )
    op.drop_index(
        "ix_school_invitations_invited_school_id", table_name="school_invitations"
    )
    op.drop_index(
        "ix_school_invitations_inviter_school_id", table_name="school_invitations"
    )
    op.drop_index("ix_school_invitations_token", table_name="school_invitations")
    op.drop_table("school_invitations")
    sa.Enum(name="enum_school_invitation_status").drop(op.get_bind())

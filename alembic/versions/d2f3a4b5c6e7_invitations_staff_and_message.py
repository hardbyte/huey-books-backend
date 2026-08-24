"""Staff invitations + custom message

Make school_invitations.inviter_school_id nullable (staff can invite any school
with no source school) and add an optional custom message.

Revision ID: d2f3a4b5c6e7
Revises: c9e1f2a3b4d5
Create Date: 2026-08-25

"""

import sqlalchemy as sa

from alembic import op

revision = "d2f3a4b5c6e7"
down_revision = "c9e1f2a3b4d5"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "school_invitations",
        "inviter_school_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=True,
    )
    op.add_column(
        "school_invitations",
        sa.Column("message", sa.String(length=2000), nullable=True),
    )

    # Deleting a school must not be blocked by historical invitations; null the
    # references instead (the invitation row survives as an audit record).
    for fk, col in (
        ("fk_invitation_inviter_school", "inviter_school_id"),
        ("fk_invitation_invited_school", "invited_school_id"),
    ):
        op.drop_constraint(fk, "school_invitations", type_="foreignkey")
        op.create_foreign_key(
            fk,
            "school_invitations",
            "schools",
            [col],
            ["wriveted_identifier"],
            ondelete="SET NULL",
        )


def downgrade():
    for fk, col in (
        ("fk_invitation_inviter_school", "inviter_school_id"),
        ("fk_invitation_invited_school", "invited_school_id"),
    ):
        op.drop_constraint(fk, "school_invitations", type_="foreignkey")
        op.create_foreign_key(
            fk,
            "school_invitations",
            "schools",
            [col],
            ["wriveted_identifier"],
        )
    op.drop_column("school_invitations", "message")
    op.alter_column(
        "school_invitations",
        "inviter_school_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=False,
    )

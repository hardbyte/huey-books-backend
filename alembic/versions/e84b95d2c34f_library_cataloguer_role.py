"""Allow library catalogue management without staff administration.

Revision ID: e84b95d2c34f
Revises: d73a84c1b23e
"""

import sqlalchemy as sa

from alembic import op

revision = "e84b95d2c34f"
down_revision = "d73a84c1b23e"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint(
        op.f("ck_library_memberships_ck_library_membership_role"),
        "library_memberships",
        type_="check",
    )
    op.create_check_constraint(
        "ck_library_membership_role",
        "library_memberships",
        "role IN ('manager', 'reviewer', 'cataloguer')",
    )


def downgrade():
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM library_memberships WHERE role = 'cataloguer')"
            )
        )
        .scalar()
    ):
        raise RuntimeError("Reconcile cataloguer grants before downgrading")
    op.drop_constraint(
        op.f("ck_library_memberships_ck_library_membership_role"),
        "library_memberships",
        type_="check",
    )
    op.create_check_constraint(
        "ck_library_membership_role",
        "library_memberships",
        "role IN ('manager', 'reviewer')",
    )

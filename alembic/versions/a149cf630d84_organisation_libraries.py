"""Add organisation membership and explicit library collection defaults."""

import sqlalchemy as sa

from alembic import op

revision = "a149cf630d84"
down_revision = "f038be529c73"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    duplicates = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT school_id FROM collections WHERE school_id IS NOT NULL GROUP BY school_id HAVING count(*) > 1 LIMIT 1"
            )
        )
        .first()
    )
    if duplicates:
        raise RuntimeError(
            "Resolve libraries with multiple legacy collections before assigning defaults"
        )
    op.create_table(
        "organisations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('school', 'public_library', 'other')", name="ck_organisation_kind"
        ),
    )
    op.add_column("schools", sa.Column("organisation_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_school_organisation",
        "schools",
        "organisations",
        ["organisation_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_schools_organisation_id", "schools", ["organisation_id"])
    op.create_table(
        "organisation_memberships",
        sa.Column(
            "organisation_id",
            sa.Uuid(),
            sa.ForeignKey("organisations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_organisation_memberships_user_id", "organisation_memberships", ["user_id"]
    )
    op.create_table(
        "library_memberships",
        sa.Column(
            "school_id",
            sa.Integer(),
            sa.ForeignKey("schools.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('manager', 'reviewer')", name="ck_library_membership_role"
        ),
    )
    op.create_index(
        "ix_library_memberships_user_id", "library_memberships", ["user_id"]
    )
    op.add_column(
        "collections",
        sa.Column(
            "is_default", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
    )
    op.execute("UPDATE collections SET is_default = true WHERE school_id IS NOT NULL")
    op.create_check_constraint(
        "ck_collections_default_school",
        "collections",
        "NOT is_default OR school_id IS NOT NULL",
    )
    op.create_index(
        "uq_collections_school_default",
        "collections",
        ["school_id"],
        unique=True,
        postgresql_where=sa.text("is_default AND school_id IS NOT NULL"),
    )


def downgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    if op.get_bind().execute(sa.text("SELECT 1 FROM organisations LIMIT 1")).first():
        raise RuntimeError(
            "Export and explicitly remove organisation data before downgrading"
        )
    if (
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM library_memberships LIMIT 1"))
        .first()
    ):
        raise RuntimeError(
            "Export and explicitly remove library memberships before downgrading"
        )
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM collections WHERE school_id IS NOT NULL AND NOT is_default LIMIT 1"
            )
        )
        .first()
    ):
        raise RuntimeError(
            "Additional library collections must be resolved before downgrading"
        )
    op.drop_index("uq_collections_school_default", table_name="collections")
    op.drop_constraint("ck_collections_default_school", "collections", type_="check")
    op.drop_column("collections", "is_default")
    op.drop_table("library_memberships")
    op.drop_table("organisation_memberships")
    op.drop_constraint("fk_school_organisation", "schools", type_="foreignkey")
    op.drop_index("ix_schools_organisation_id", table_name="schools")
    op.drop_column("schools", "organisation_id")
    op.drop_table("organisations")

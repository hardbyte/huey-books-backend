"""Add independent library and education identities.

Revision ID: d73a84c1b23e
Revises: c62f73b0a12d
"""

import sqlalchemy as sa

from alembic import op

revision = "d73a84c1b23e"
down_revision = "c62f73b0a12d"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("libraries", "education_units"):
        op.create_table(
            table,
            sa.Column("id", sa.UUID(), primary_key=True),
            sa.Column(
                "organisation_id",
                sa.UUID(),
                sa.ForeignKey("organisations.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("archived_at", sa.DateTime(timezone=True)),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.UniqueConstraint("organisation_id", "id"),
        )
        op.create_index(f"ix_{table}_organisation_id", table, ["organisation_id"])
    op.create_table(
        "education_unit_libraries",
        sa.Column("education_unit_id", sa.UUID(), primary_key=True),
        sa.Column("library_id", sa.UUID(), primary_key=True),
        sa.Column("organisation_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organisation_id", "education_unit_id"],
            ["education_units.organisation_id", "education_units.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "library_id"],
            ["libraries.organisation_id", "libraries.id"],
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_education_unit_libraries_library_id",
        "education_unit_libraries",
        ["library_id"],
    )
    op.create_index(
        "ix_education_unit_libraries_organisation_id",
        "education_unit_libraries",
        ["organisation_id"],
    )


def downgrade():
    connection = op.get_bind()
    if connection.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM libraries) OR EXISTS (SELECT 1 FROM education_units)"
        )
    ).scalar():
        raise RuntimeError(
            "Reconcile library and education identities before downgrading"
        )
    op.drop_table("education_unit_libraries")
    op.drop_table("education_units")
    op.drop_table("libraries")

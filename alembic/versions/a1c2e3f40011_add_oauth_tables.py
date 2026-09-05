"""
Add OAuth authorization-server tables: grants, authorization codes, refresh tokens

Revision ID: a1c2e3f40011
Revises: d3e1f2a4b5c6
Create Date: 2026-09-03 00:00:00.000000
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "a1c2e3f40011"
down_revision = "d3e1f2a4b5c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "oauth_grants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("school_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", sa.String(255), nullable=False),
        sa.Column("scopes", sa.String(1024), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(now() at time zone 'utc')"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_oauth_grant_user", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_oauth_grants_user_id", "oauth_grants", ["user_id"])
    op.create_index("ix_oauth_grants_school_id", "oauth_grants", ["school_id"])

    op.create_table(
        "oauth_authorization_codes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("grant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("redirect_uri", sa.String(2048), nullable=False),
        sa.Column("code_challenge", sa.String(255), nullable=False),
        sa.Column(
            "code_challenge_method",
            sa.String(10),
            nullable=False,
            server_default="S256",
        ),
        sa.Column("scopes", sa.String(1024), nullable=False, server_default=""),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(now() at time zone 'utc')"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["grant_id"],
            ["oauth_grants.id"],
            name="fk_oauth_code_grant",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_oauth_codes_grant_id", "oauth_authorization_codes", ["grant_id"]
    )

    op.create_table(
        "oauth_refresh_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("grant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("family_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scopes", sa.String(1024), nullable=False, server_default=""),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(now() at time zone 'utc')"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["grant_id"],
            ["oauth_grants.id"],
            name="fk_oauth_refresh_grant",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_oauth_refresh_family", "oauth_refresh_tokens", ["family_id"])
    op.create_index("ix_oauth_refresh_grant_id", "oauth_refresh_tokens", ["grant_id"])


def downgrade() -> None:
    op.drop_index("ix_oauth_refresh_grant_id", table_name="oauth_refresh_tokens")
    op.drop_index("ix_oauth_refresh_family", table_name="oauth_refresh_tokens")
    op.drop_table("oauth_refresh_tokens")
    op.drop_index("ix_oauth_codes_grant_id", table_name="oauth_authorization_codes")
    op.drop_table("oauth_authorization_codes")
    op.drop_index("ix_oauth_grants_school_id", table_name="oauth_grants")
    op.drop_index("ix_oauth_grants_user_id", table_name="oauth_grants")
    op.drop_table("oauth_grants")

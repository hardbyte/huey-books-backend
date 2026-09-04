"""Add shared MCP OAuth and school-selection storage.

Revision ID: a1c2e3f40012
Revises: a1c2e3f40011
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "a1c2e3f40012"
down_revision = "a1c2e3f40011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    table = sa.Table(
        "mcp_oauth_proxy_kv",
        sa.MetaData(),
        sa.Column("collection", sa.Text(), primary_key=True),
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("value", postgresql.JSONB(), nullable=False),
        sa.Column("ttl", sa.Double()),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
    )
    # Staging may already contain the table created by the key-value library.
    table.create(op.get_bind(), checkfirst=True)
    op.create_index(
        "idx_mcp_oauth_proxy_kv_expires_at",
        "mcp_oauth_proxy_kv",
        ["expires_at"],
        postgresql_where=sa.text("expires_at IS NOT NULL"),
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_table("mcp_oauth_proxy_kv")

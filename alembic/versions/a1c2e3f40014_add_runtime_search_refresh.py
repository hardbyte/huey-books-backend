"""Add runtime search refresh function.

Revision ID: a1c2e3f40014
Revises: a1c2e3f40013
"""

from alembic_utils.pg_function import PGFunction
from sqlalchemy import text

from alembic import op

revision = "a1c2e3f40014"
down_revision = "a1c2e3f40013"
branch_labels = None
depends_on = None

refresh_search_index = PGFunction(
    schema="public",
    signature="refresh_search_index()",
    definition="""returns void LANGUAGE plpgsql SECURITY DEFINER
      SET search_path = pg_catalog, pg_temp
      AS $function$
        BEGIN
          REFRESH MATERIALIZED VIEW public.search_view_v1;
        END;
      $function$
    """,
)


def upgrade() -> None:
    op.create_entity(refresh_search_index)
    op.execute("REVOKE ALL ON FUNCTION public.refresh_search_index() FROM PUBLIC")
    if (
        op.get_bind()
        .execute(text("SELECT 1 FROM pg_roles WHERE rolname = 'cloudrun'"))
        .scalar()
    ):
        op.execute(
            "GRANT EXECUTE ON FUNCTION public.refresh_search_index() TO cloudrun"
        )


def downgrade() -> None:
    op.drop_entity(refresh_search_index)

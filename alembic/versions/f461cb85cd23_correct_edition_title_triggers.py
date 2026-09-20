"""Derive edition titles from the associated work.

Revision ID: f461cb85cd23
Revises: e350ba74bc12
"""

from alembic import op

revision = "f461cb85cd23"
down_revision = "e350ba74bc12"
branch_labels = None
depends_on = None

NEW_EDITION = """
    UPDATE public.editions
    SET title = COALESCE(NEW.edition_title, (SELECT title FROM public.works WHERE id = NEW.work_id))
    WHERE id = NEW.id;
"""

OLD_EDITION = """
    UPDATE editions SET title = COALESCE(editions.edition_title, works.title)
    FROM works
    WHERE editions.id = NEW.id AND (NEW.edition_title IS NOT NULL OR NEW.work_id IS NOT NULL);
"""

NEW_WORK = """
        UPDATE public.editions SET title = COALESCE(edition_title, NEW.title)
        WHERE work_id = NEW.id;
"""

OLD_WORK = """
        UPDATE editions SET title = COALESCE(editions.edition_title, works.title)
        FROM works
        WHERE editions.work_id = NEW.id AND (NEW.title IS NOT NULL);
"""


def replace_functions(edition_body: str, work_body: str) -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '120s'")
    for name, body in [
        ("update_edition_title", edition_body),
        ("update_edition_title_from_work", work_body),
    ]:
        op.execute(
            f"CREATE OR REPLACE FUNCTION public.{name}() RETURNS trigger LANGUAGE plpgsql AS $function$ BEGIN {body} RETURN NULL; END; $function$"
        )


def upgrade() -> None:
    replace_functions(NEW_EDITION, NEW_WORK)
    op.execute("""
        UPDATE public.editions e
        SET title = coalesce(e.edition_title, w.title)
        FROM public.works w
        WHERE w.id = e.work_id AND e.title IS DISTINCT FROM coalesce(e.edition_title, w.title)
    """)
    op.execute("""
        UPDATE public.editions
        SET title = edition_title
        WHERE work_id IS NULL AND title IS DISTINCT FROM edition_title
    """)


def downgrade() -> None:
    replace_functions(OLD_EDITION, OLD_WORK)

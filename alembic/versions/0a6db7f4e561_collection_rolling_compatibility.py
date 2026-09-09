"""Preserve omitted legacy collection defaults during rolling deployment."""

import sqlalchemy as sa
from alembic_utils.pg_function import PGFunction
from alembic_utils.pg_trigger import PGTrigger

from alembic import op

revision = "0a6db7f4e561"
down_revision = "f95ca6e3d450"
branch_labels = None
depends_on = None

public_collection_legacy_default = PGFunction(
    schema="public",
    signature="collection_legacy_default()",
    definition="returns trigger LANGUAGE plpgsql\n    SET search_path = pg_catalog, pg_temp\n    AS $function$\n    BEGIN\n        IF NEW.school_id IS NOT NULL THEN\n            PERFORM 1 FROM public.schools\n            WHERE wriveted_identifier = NEW.school_id FOR UPDATE;\n        END IF;\n        IF NEW.is_default IS NULL THEN\n            IF NEW.school_id IS NULL THEN\n                NEW.is_default := false;\n            ELSE\n                IF EXISTS (SELECT 1 FROM public.collections WHERE school_id = NEW.school_id) THEN\n                    RAISE EXCEPTION 'Legacy collection writer requires an empty library'\n                        USING ERRCODE = '23514';\n                END IF;\n                NEW.is_default := true;\n            END IF;\n        END IF;\n        RETURN NEW;\n    END;\n    $function$",
)

public_collections_collection_legacy_default_trigger = PGTrigger(
    schema="public",
    signature="collection_legacy_default_trigger",
    on_entity="public.collections",
    is_constraint=False,
    definition="BEFORE INSERT ON public.collections FOR EACH ROW EXECUTE FUNCTION public.collection_legacy_default()",
)


def upgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("LOCK TABLE collections IN SHARE ROW EXCLUSIVE MODE")
    ambiguous = op.get_bind().scalar(
        sa.text("""
        SELECT EXISTS (
            SELECT school_id FROM collections WHERE school_id IS NOT NULL
            GROUP BY school_id HAVING count(*) > 1 AND NOT bool_or(is_default)
        )
    """)
    )
    if ambiguous:
        raise RuntimeError(
            "Resolve libraries with multiple collections and no default before upgrading"
        )
    op.execute("""
        UPDATE collections SET is_default = true
        WHERE school_id IN (
            SELECT school_id FROM collections WHERE school_id IS NOT NULL
            GROUP BY school_id HAVING count(*) = 1 AND NOT bool_or(is_default)
        )
    """)
    op.create_entity(public_collection_legacy_default)
    op.create_entity(public_collections_collection_legacy_default_trigger)
    op.alter_column("collections", "is_default", server_default=None)


def downgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.alter_column("collections", "is_default", server_default=sa.text("false"))
    op.drop_entity(public_collections_collection_legacy_default_trigger)
    op.drop_entity(public_collection_legacy_default)

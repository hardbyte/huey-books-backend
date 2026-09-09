from alembic.autogenerate import produce_migrations
from alembic.migration import MigrationContext
from app.db import Base
from app.models.organisation import organisation_billing_links


def test_retained_billing_links_match_declarative_schema(session):
    table_name = "organisation_billing_links"
    assert Base.metadata.tables[table_name] is organisation_billing_links
    context = MigrationContext.configure(
        session.connection(),
        opts={
            "compare_server_default": True,
            "include_schemas": False,
            "include_name": lambda name, kind, parents: (
                kind != "table" or name == table_name
            ),
            "include_object": lambda obj, name, kind, reflected, compare_to: (
                kind != "table" or name == table_name
            ),
        },
    )
    migration = produce_migrations(context, Base.metadata)
    assert migration.upgrade_ops.is_empty(), migration.upgrade_ops.as_diffs()

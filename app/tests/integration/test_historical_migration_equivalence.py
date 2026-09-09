import ast
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import Column, Integer, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import declarative_base

from alembic.migration import MigrationContext
from alembic.operations import Operations

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "app/tests/fixtures/historical_migrations"


def load_revision(path, operations, models):
    tree = ast.parse(path.read_text())
    tree.body = [
        node
        for node in tree.body
        if not (isinstance(node, ast.ImportFrom) and node.module == "app.models")
    ]
    namespace = {"__name__": "historical_revision", **models}
    exec(compile(tree, str(path), "exec"), namespace)
    namespace["op"] = operations
    return SimpleNamespace(**namespace)


def replay(engine, filename, original):
    with engine.connect() as connection, connection.begin():
        schema = "replay_" + uuid4().hex
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
        base = declarative_base()

        class HistoricalSchool(base):
            __tablename__ = "schools"
            id = Column(Integer, primary_key=True)
            info = Column(MutableDict.as_mutable(JSONB), nullable=True)

        class HistoricalCollectionItem(base):
            __tablename__ = "collection_items"
            id = Column(Integer, primary_key=True)
            school_id = Column(Integer)
            edition_id = Column(Integer)

        base.metadata.create_all(connection, checkfirst=False)
        schools = HistoricalSchool.__table__
        items = HistoricalCollectionItem.__table__
        connection.execute(
            schools.insert(),
            [
                {"id": 1, "info": None},
                {"id": 2, "info": {}},
                {"id": 3, "info": {"location": {"state": "NZ"}, "custom": [1, 2]}},
                {"id": 4, "info": {"experiments": {"old": True}, "keep": "yes"}},
            ],
        )
        connection.execute(
            items.insert(),
            [
                {"id": 1, "school_id": 7, "edition_id": 9},
                {"id": 2, "school_id": 7, "edition_id": 9},
            ],
        )
        path = (
            FIXTURES / (filename + ".txt")
            if original
            else ROOT / "alembic/versions" / filename
        )
        revision = load_revision(
            path,
            Operations(MigrationContext.configure(connection)),
            {
                "School": HistoricalSchool,
                "CollectionItem": HistoricalCollectionItem,
            },
        )

        def snapshot():
            return (
                connection.execute(select(schools).order_by(schools.c.id)).all(),
                connection.execute(select(items).order_by(items.c.id)).all(),
                connection.execute(
                    text("""
                    SELECT pg_get_constraintdef(oid) FROM pg_constraint
                    WHERE connamespace = current_schema()::regnamespace
                    ORDER BY conname
                """)
                ).all(),
            )

        revision.upgrade()
        upgraded = snapshot()
        revision.downgrade()
        downgraded = snapshot()
        revision.upgrade()
        reapplied = snapshot()
        connection.rollback()
        return upgraded, downgraded, reapplied


@pytest.mark.parametrize(
    "filename",
    [
        "13ca81ae5800_add_experiments_to_schools.py",
        "ded4fe3ab668_add_unique_constraint_on_editions_per_.py",
    ],
)
def test_historical_replay_preserves_data_and_constraints(session, filename):
    engine = session.get_bind()
    assert replay(engine, filename, original=False) == replay(
        engine, filename, original=True
    )

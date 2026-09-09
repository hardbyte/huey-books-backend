import importlib.util
from pathlib import Path

from sqlalchemy import JSON, Column, Integer, MetaData, Table, create_engine, select


def test_experiments_migration_uses_only_revision_local_columns(monkeypatch):
    path = (
        Path(__file__).resolve().parents[3]
        / "alembic/versions/13ca81ae5800_add_experiments_to_schools.py"
    )
    spec = importlib.util.spec_from_file_location("historical_school_experiments", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    metadata = MetaData()
    schools = Table(
        "schools",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("info", JSON),
    )
    metadata.create_all(engine)
    original = {"location": {"state": "NZ"}, "custom": "preserved"}
    with engine.begin() as connection:
        connection.execute(
            schools.insert(), [{"id": 1, "info": original}, {"id": 2, "info": None}]
        )
        monkeypatch.setattr(migration.op, "get_bind", lambda: connection)
        migration.upgrade()
        rows = connection.execute(select(schools).order_by(schools.c.id)).all()
        assert rows[0].info == {
            **original,
            "experiments": {"no-jokes": False, "no-choice-option": True},
        }
        assert rows[1].info["location"] == {
            "suburb": None,
            "state": "Unknown",
            "postcode": "",
        }
        migration.downgrade()
        rows = connection.execute(select(schools).order_by(schools.c.id)).all()
        assert rows[0].info == original
        assert "experiments" not in rows[1].info
    engine.dispose()

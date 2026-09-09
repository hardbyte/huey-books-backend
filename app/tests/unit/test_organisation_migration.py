import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import create_engine


def test_downgrade_preserves_standalone_library_memberships(monkeypatch):
    path = (
        Path(__file__).resolve().parents[3]
        / "alembic/versions/a149cf630d84_organisation_libraries.py"
    )
    spec = importlib.util.spec_from_file_location(
        "organisation_libraries_migration", path
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE organisations (id INTEGER)")
        connection.exec_driver_sql(
            "CREATE TABLE library_memberships (school_id INTEGER)"
        )
        connection.exec_driver_sql("INSERT INTO library_memberships VALUES (1)")
        monkeypatch.setattr(migration.op, "get_bind", lambda: connection)
        monkeypatch.setattr(migration.op, "execute", lambda statement: None)
        with pytest.raises(RuntimeError, match="library memberships"):
            migration.downgrade()
        assert (
            connection.exec_driver_sql(
                "SELECT school_id FROM library_memberships"
            ).scalar_one()
            == 1
        )
    engine.dispose()

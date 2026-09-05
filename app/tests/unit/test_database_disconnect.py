from app.api.version import get_version
from app.db.session import database_connection


def test_version_recovers_closed_idle_connection(tmp_path):
    engine, session_factory = database_connection(
        f"sqlite:///{tmp_path / 'database.db'}", pool_size=1, max_overflow=0
    )
    try:
        with engine.connect() as connection:
            driver_connection = connection.connection.driver_connection
        driver_connection.close()

        with session_factory() as session:
            assert get_version(session)["database_revision"] == "development"
    finally:
        engine.dispose()

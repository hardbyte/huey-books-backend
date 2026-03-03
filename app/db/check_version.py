import logging

from tenacity import after_log, before_log, retry, stop_after_attempt, wait_fixed

from alembic.runtime.migration import MigrationContext
from app.config import Settings, get_settings
from app.db.session import get_session

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

max_tries = 60 * 5  # 5 minutes
wait_seconds = 1


@retry(
    stop=stop_after_attempt(max_tries),
    wait=wait_fixed(wait_seconds),
    before=before_log(logger, logging.DEBUG),
    after=after_log(logger, logging.INFO),
)
def check_database_ready(config: Settings) -> None:
    session_generator = get_session(config)
    session = next(session_generator)

    try:
        session.execute("SELECT 1")
        print("select worked")

        # Now check it has the schema version that we require
        expected_database_version = MigrationContext.configure(
            session.connection()
        ).get_current_revision()

        res = session.execute("SELECT version_num from alembic_version")
        current_version = res.fetchall()[0][0]
        logger.info(f"Current Alembic database schema version: {current_version}")
        logger.info(f"Expected database schema version: {expected_database_version}")
        assert current_version == expected_database_version, (
            "Unexpected database revision"
        )
    except Exception as e:
        logger.error(e)
        raise e


def check_database_ready_with_retry(config):
    logger.info("Waiting for database to accept connections")
    check_database_ready(config)
    logger.info("Database ready")


if __name__ == "__main__":
    config = get_settings()
    check_database_ready_with_retry(config)

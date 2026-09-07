"""Dry-run or complete legacy school attribution after old API revisions drain."""

import argparse
import os
from datetime import datetime, timezone

from sqlalchemy import create_engine, text

MATCHING_SESSIONS = """
SELECT session.id, school.wriveted_identifier AS school_id
FROM conversation_sessions session JOIN schools school
  ON lower(session.state #>> '{context,school_wriveted_id}') = school.wriveted_identifier::text
WHERE session.school_id IS NULL
  AND session.info ->> 'school_attribution_version' IS NULL
  AND session.started_at < :before
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--before",
        required=True,
        help="Old-revision drain time, ISO 8601 with timezone",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write backfill; default only counts candidates",
    )
    arguments = parser.parse_args()
    before = datetime.fromisoformat(arguments.before)
    if before.tzinfo is None:
        parser.error("--before must include a timezone")
    parameters = {"before": before.astimezone(timezone.utc).replace(tzinfo=None)}
    engine = create_engine(os.environ["SQLALCHEMY_DATABASE_URI"])
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text("SET lock_timeout = '5s'"))
        connection.execute(text("SET statement_timeout = '30s'"))
        count = connection.execute(
            text(f"SELECT count(*) FROM ({MATCHING_SESSIONS}) candidates"), parameters
        ).scalar_one()
        print(f"Matching legacy sessions: {count}")
        if arguments.apply:
            updated = 0
            while True:
                result = connection.execute(
                    text(f"""
                    WITH batch AS ({MATCHING_SESSIONS} ORDER BY session.id LIMIT 1000)
                    UPDATE conversation_sessions session SET school_id = batch.school_id
                    FROM batch WHERE session.id = batch.id AND session.school_id IS NULL
                """),
                    parameters,
                )
                updated += result.rowcount
                if result.rowcount == 0:
                    break
            print(f"Updated legacy sessions: {updated}")
    engine.dispose()


if __name__ == "__main__":
    main()

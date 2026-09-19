"""Export an allowlisted, read-only catalogue snapshot for isolated evaluation."""

import argparse
import hashlib
import json
import os
import time
from pathlib import Path

import psycopg2

QUERIES = {
    "works": """
        WITH author_data AS (
          SELECT awa.work_id, jsonb_agg(jsonb_build_object('id', a.id, 'name',
              concat_ws(' ', a.first_name, a.last_name)) ORDER BY a.id) AS authors
          FROM author_work_association awa JOIN authors a ON a.id=awa.author_id GROUP BY awa.work_id
        ), series_data AS (
          SELECT swa.work_id, jsonb_agg(jsonb_build_object('id', s.id, 'title', s.title)
              ORDER BY s.id) AS series
          FROM series_works_association swa JOIN series s ON s.id=swa.series_id GROUP BY swa.work_id
        )
        SELECT w.id, w.title, w.subtitle, w.leading_article,
          coalesce(a.authors, '[]'::jsonb), coalesce(s.series, '[]'::jsonb)
        FROM works w LEFT JOIN author_data a ON a.work_id=w.id
        LEFT JOIN series_data s ON s.work_id=w.id ORDER BY w.id
    """,
    "legacy_search": "SELECT work_id, series_id, document FROM search_view_v1 ORDER BY work_id, series_id NULLS FIRST",
    "popularity": "SELECT work_id, collection_frequency FROM work_collection_frequency ORDER BY work_id",
    "editions": "SELECT id, work_id, isbn, title FROM editions ORDER BY id",
    "membership": """
        WITH scopes AS (SELECT school_id, row_number() OVER (ORDER BY school_id) AS scope_id
          FROM (SELECT DISTINCT school_id FROM collections WHERE school_id IS NOT NULL) c)
        SELECT s.scope_id, e.work_id, bool_or(ci.copies_available>0) AS available
        FROM scopes s JOIN collections c ON c.school_id=s.school_id
        JOIN collection_items ci ON ci.collection_id=c.id
        JOIN editions e ON e.isbn=ci.edition_isbn
        WHERE ci.copies_total>0 AND e.work_id IS NOT NULL
        GROUP BY s.scope_id,e.work_id ORDER BY s.scope_id,e.work_id
    """,
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    args.output.mkdir(parents=True, exist_ok=False)
    with psycopg2.connect(
        host="127.0.0.1",
        port=args.port,
        dbname=os.environ.get("PGDATABASE", "postgres"),
        application_name="search-evaluation-export",
        options="-c statement_timeout=60000 -c lock_timeout=2000",
    ) as connection:
        connection.set_session(readonly=True, isolation_level="REPEATABLE READ")
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT now(), version(), current_setting('transaction_read_only')"
            )
            timestamp, version, readonly = cursor.fetchone()
            assert readonly == "on"
            cursor.execute(
                "SELECT extname,extversion FROM pg_extension WHERE extname IN ('pg_trgm','pg_textsearch')"
            )
            manifest = {
                "snapshot_at": str(timestamp),
                "server": version,
                "extensions": dict(cursor.fetchall()),
                "files": {},
            }
            cursor.execute("SELECT * FROM search_index_refreshes ORDER BY index_name")
            manifest["refreshes"] = [
                dict(zip([d.name for d in cursor.description], row)) for row in cursor
            ]
            for name, query in QUERIES.items():
                start = time.monotonic()
                path = args.output / f"{name}.csv"
                with path.open("w") as stream:
                    cursor.copy_expert(f"COPY ({query}) TO STDOUT WITH CSV", stream)
                details = {
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "bytes": path.stat().st_size,
                    "seconds": round(time.monotonic() - start, 2),
                }
                manifest["files"][name] = details
                print(json.dumps({"export": name, **details}), flush=True)
            (args.output / "manifest.json").write_text(
                json.dumps(manifest, indent=2, default=str)
            )


if __name__ == "__main__":
    main()

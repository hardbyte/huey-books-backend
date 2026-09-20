"""Load the exported catalogue into a dedicated local benchmark database."""

import argparse
import hashlib
import json
import time
from pathlib import Path

import psycopg2

TABLES = {
    "works": "work_id integer PRIMARY KEY, title text, subtitle text, leading_article text, authors jsonb, series jsonb",
    "legacy_search": "work_id integer, series_id integer, document tsvector",
    "popularity": "work_id integer UNIQUE, frequency bigint",
    "editions": "edition_id integer PRIMARY KEY, work_id integer, isbn text, title text",
    "membership": "scope_id integer, work_id integer, available boolean, PRIMARY KEY(scope_id,work_id)",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--database", required=True)
    args = parser.parse_args()
    if not args.database.startswith("search_eval_"):
        parser.error("Use a dedicated search_eval_ database")
    manifest = json.loads((args.snapshot / "manifest.json").read_text())
    connection = psycopg2.connect(
        host="127.0.0.1",
        port=55530,
        user="postgres",
        password="password",
        dbname=args.database,
    )
    connection.autocommit = True
    measurements = {}
    with connection.cursor() as cursor:
        cursor.execute(
            "CREATE EXTENSION IF NOT EXISTS pg_trgm; CREATE EXTENSION IF NOT EXISTS pg_textsearch"
        )
        cursor.execute("SET statement_timeout='120s'")
        for name, columns in TABLES.items():
            path = args.snapshot / f"{name}.csv"
            assert (
                hashlib.sha256(path.read_bytes()).hexdigest()
                == manifest["files"][name]["sha256"]
            )
            cursor.execute(f"CREATE TABLE {name} ({columns})")
            with path.open() as stream:
                cursor.copy_expert(f"COPY {name} FROM STDIN WITH CSV", stream)
            cursor.execute(f"SELECT count(*) FROM {name}")
            print(json.dumps({"table": name, "rows": cursor.fetchone()[0]}), flush=True)
        cursor.execute("""
          CREATE TABLE documents AS SELECT w.*,
          coalesce((SELECT string_agg(a->>'name',' ' ORDER BY a->>'id') FROM jsonb_array_elements(authors) a),'') AS author_text,
          coalesce((SELECT string_agg(s->>'title',' ' ORDER BY s->>'id') FROM jsonb_array_elements(series) s),'') AS series_text,
          coalesce(p.frequency,0) AS frequency, p.work_id IS NOT NULL AS has_popularity
          FROM works w LEFT JOIN popularity p USING(work_id);
          ALTER TABLE documents ADD PRIMARY KEY(work_id);
          ALTER TABLE documents ADD COLUMN content text;
          UPDATE documents SET content=concat_ws(' ',title,subtitle,author_text,series_text);
          ALTER TABLE documents ADD COLUMN document tsvector;
          ALTER TABLE documents ADD COLUMN weighted tsvector;
          UPDATE documents SET document=to_tsvector('english',content),
            weighted=setweight(to_tsvector('english',coalesce(title,'')),'A') ||
                     setweight(to_tsvector('english',coalesce(subtitle,'')),'C') ||
                     setweight(to_tsvector('english',author_text),'C') ||
                     setweight(to_tsvector('english',series_text),'B');
        """)
        indexes = {
            "legacy_document_gin": "ON legacy_search USING gin(document)",
            "legacy_work_idx": "ON legacy_search(work_id)",
            "editions_work_idx": "ON editions(work_id)",
            "editions_isbn_idx": "ON editions(isbn)",
            "documents_fts": "ON documents USING gin(document)",
            "documents_weighted": "ON documents USING gin(weighted)",
            "documents_title_trgm": "ON documents USING gin(lower(title) gin_trgm_ops)",
            "documents_content_trgm": "ON documents USING gin(lower(content) gin_trgm_ops)",
            "documents_title_gist": "ON documents USING gist(lower(title) gist_trgm_ops)",
            "documents_bm25": "ON documents USING bm25(content) WITH(text_config='english')",
        }
        for name, definition in indexes.items():
            started = time.monotonic()
            cursor.execute(f"CREATE INDEX {name} {definition}")
            cursor.execute("SELECT pg_relation_size(%s::regclass)", (name,))
            measurements[name] = {
                "seconds": round(time.monotonic() - started, 3),
                "bytes": cursor.fetchone()[0],
            }
            print(json.dumps({"index": name, **measurements[name]}), flush=True)
        cursor.execute("ANALYZE")
        cursor.execute("SELECT version()")
        measurements["server"] = cursor.fetchone()[0]
        cursor.execute(
            "SELECT extname,extversion FROM pg_extension WHERE extname IN ('pg_textsearch','pg_trgm')"
        )
        measurements["extensions"] = dict(cursor.fetchall())
        (args.snapshot.parent / "index-builds.json").write_text(
            json.dumps(measurements, indent=2)
        )
    connection.close()


if __name__ == "__main__":
    main()

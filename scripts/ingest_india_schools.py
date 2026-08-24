"""Import Indian schools from the UDISE+ dataset (India Data Portal CKAN) as
INACTIVE reference schools, so they are findable during self-serve signup.

The rows mirror the AUS/NZL reference schools: state INACTIVE, bookbot_type
HUEY_BOOKS, and an ``info.location`` block. ``official_identifier`` is the UDISE
school code; every imported row is tagged ``info->>'source' = 'udise'`` so the
import can be identified or rolled back in bulk:

    DELETE FROM schools
    WHERE country_code = 'IND' AND info->>'source' = 'udise';

The import is idempotent — it upserts on the unique
``(country_code, official_identifier)`` index, so re-running refreshes names/info
without creating duplicates. Manually-curated India schools that use a different
identifier scheme (e.g. the IB code ``IB:003159`` for American International
School Chennai) are untouched.

Requires read/write DB access. Point it at the database with --database-url or
the SQLALCHEMY_DATABASE_URI env var (any +asyncpg/+psycopg2 suffix is stripped).

Examples:
    # one state (recommended first scope)
    uv run python scripts/ingest_india_schools.py --state "Tamil Nadu"
    # count only, no writes
    uv run python scripts/ingest_india_schools.py --state "Kerala" --dry-run
    # everything (~1.37M schools — large; see README notes)
    uv run python scripts/ingest_india_schools.py --all
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import httpx
import psycopg2
from psycopg2.extras import execute_values

CKAN_URL = "https://ckandev.indiadataportal.com/api/3/action/datastore_search"
# "UDISE - Basic Details of Schools" resource on the India Data Portal.
RESOURCE_ID = "457fddf1-982f-4c85-855d-5095578accc1"
PAGE = 10_000

UPSERT_SQL = """
INSERT INTO schools (country_code, official_identifier, name, state, bookbot_type, info)
VALUES %s
ON CONFLICT (country_code, official_identifier)
DO UPDATE SET name = EXCLUDED.name, info = EXCLUDED.info
"""
# execute_values fills %s per row-tuple: (official_identifier, name, info_json)
ROW_TEMPLATE = "('IND', %s, %s, 'INACTIVE', 'HUEY_BOOKS', %s::jsonb)"


def normalize_db_url(url: str) -> str:
    """psycopg2 wants a bare postgresql:// URL (no SQLAlchemy driver suffix)."""
    return url.replace("+asyncpg", "").replace("+psycopg2", "")


def fetch_pages(state: str | None):
    """Yield lists of UDISE records, paging through the CKAN datastore."""
    offset = 0
    params = {"resource_id": RESOURCE_ID, "limit": PAGE}
    if state:
        params["filters"] = json.dumps({"state_name": state})
    with httpx.Client(
        timeout=120, headers={"User-Agent": "huey-books-udise-import"}
    ) as client:
        while True:
            resp = client.get(CKAN_URL, params={**params, "offset": offset})
            resp.raise_for_status()
            records = resp.json()["result"]["records"]
            if not records:
                return
            yield records
            offset += len(records)
            if len(records) < PAGE:
                return


def to_row(rec: dict):
    """Map a UDISE record to (official_identifier, name, info_json) or None."""
    code = str(rec.get("udise_school_code") or "").strip()
    name = (rec.get("school_name") or "").strip()
    if not code or not name:
        return None
    info = {
        "source": "udise",
        "location": {
            "state": rec.get("state_name"),
            "district": rec.get("district_name"),
            "suburb": rec.get("village_name"),
            "postcode": str(rec.get("pincode") or "") or None,
            "lat": rec.get("latitude"),
            "long": rec.get("longitude"),
        },
        "category": rec.get("school_category"),
        "management": rec.get("management"),
        "board_secondary": rec.get("aff_board_sec"),
        "board_higher_secondary": rec.get("aff_board_h_sec"),
        "year_of_establishment": rec.get("year_of_establishment"),
        "status": rec.get("status"),
    }
    return (code, name[:256], json.dumps(info))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--state", help='UDISE state_name, e.g. "Tamil Nadu"')
    scope.add_argument(
        "--all", action="store_true", help="import every Indian school (~1.37M)"
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("SQLALCHEMY_DATABASE_URI")
        or os.environ.get("DATABASE_URL"),
        help="postgresql:// URL (defaults to SQLALCHEMY_DATABASE_URI)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="count only, make no writes"
    )
    args = parser.parse_args()

    if not args.database_url:
        sys.exit("No database URL: pass --database-url or set SQLALCHEMY_DATABASE_URI")

    scope_label = "ALL India" if args.all else args.state
    print(f"Importing UDISE schools: {scope_label}{' (dry run)' if args.dry_run else ''}")

    conn = None if args.dry_run else psycopg2.connect(normalize_db_url(args.database_url))
    total = skipped = 0
    try:
        for page in fetch_pages(None if args.all else args.state):
            rows = []
            for rec in page:
                row = to_row(rec)
                if row is None:
                    skipped += 1
                else:
                    rows.append(row)
            # De-duplicate by official_identifier within the batch: a single
            # INSERT ... ON CONFLICT can't affect the same row twice, so a
            # repeated UDISE code in one page would otherwise error. Keep last.
            if rows:
                rows = list({row[0]: row for row in rows}.values())
            if rows and not args.dry_run:
                with conn.cursor() as cur:
                    execute_values(cur, UPSERT_SQL, rows, template=ROW_TEMPLATE, page_size=1000)
                conn.commit()  # commit per page so long runs make steady progress
            total += len(rows)
            print(f"  upserted {total} (skipped {skipped})...", flush=True)
    finally:
        if conn is not None:
            conn.close()

    verb = "would upsert" if args.dry_run else "upserted"
    print(f"Done. {verb} {total} India schools ({scope_label}); skipped {skipped} rows.")


if __name__ == "__main__":
    main()

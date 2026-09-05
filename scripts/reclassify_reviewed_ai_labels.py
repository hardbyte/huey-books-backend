"""Reclassify only the two audited Chennai batches; dry-run by default."""

import argparse
import json

from sqlalchemy import text

from app.db.session import get_session_maker

RUNS = ("chennai-20260905", "chennai-20260905-round2")
ORIGIN_COLUMNS = (
    "hue_origin",
    "reading_ability_origin",
    "age_origin",
    "summary_origin",
)


def reclassify(session, expected_count=200):
    session.execute(text("SET LOCAL lock_timeout = '5s'"))
    session.execute(text("SET LOCAL statement_timeout = '30s'"))
    rows = (
        session.execute(
            text("""
        SELECT id, work_id, info, hue_origin::text, reading_ability_origin::text,
               age_origin::text, summary_origin::text, checked
        FROM labelsets
        WHERE info->'reviewed_ai_labelling'->>'run' IN (:first, :second)
        ORDER BY id FOR UPDATE
    """),
            {"first": RUNS[0], "second": RUNS[1]},
        )
        .mappings()
        .all()
    )
    if (
        len(rows) != expected_count
        or len({row["work_id"] for row in rows}) != expected_count
    ):
        raise ValueError(
            f"Expected {expected_count} distinct audited works; found {len(rows)}"
        )
    changes = []
    for row in rows:
        provenance = row["info"]["reviewed_ai_labelling"]
        if provenance.get("human_reviewed") is not False or not provenance.get(
            "research_model"
        ):
            raise ValueError(f"Missing AI provenance for work {row['work_id']}")
        before = provenance.get("before")
        if not isinstance(before, dict):
            raise ValueError(f"Missing before-image for work {row['work_id']}")
        assigned_columns = ["hue_origin", "reading_ability_origin"]
        if before.get("min_age") is None and before.get("max_age") is None:
            assigned_columns.append("age_origin")
        if not before.get("huey_summary"):
            assigned_columns.append("summary_origin")
        columns = [column for column in assigned_columns if row[column] == "OTHER"]
        if not columns:
            continue
        # A subsequent human decision must be examined, not automatically reclassified.
        if row["checked"] is not None:
            raise ValueError(f"Work {row['work_id']} has a subsequent review")
        assignments = ", ".join(
            f"{column} = 'AI_ASSISTED'::labelorigin" for column in columns
        )
        session.execute(
            text(f"UPDATE labelsets SET {assignments} WHERE id = :id"),
            {"id": row["id"]},
        )
        changes.append(
            {
                "work_id": row["work_id"],
                "labelset_id": row["id"],
                "columns": columns,
                "run": provenance["run"],
            }
        )
    return changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    with get_session_maker()() as session:
        changes = reclassify(session)
        if args.commit:
            session.commit()
        else:
            session.rollback()
    print(
        json.dumps(
            {
                "committed": args.commit,
                "works_changed": len(changes),
                "changes": changes,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

COHORT = """
SELECT id, started_at, state FROM conversation_sessions
WHERE school_id = CAST(:school_id AS uuid)
  AND started_at >= :start AND started_at < :end
"""

HOLDINGS = """
SELECT DISTINCT e.work_id
FROM collections c
JOIN collection_items ci ON ci.collection_id = c.id
JOIN editions e ON e.isbn = ci.edition_isbn
WHERE c.school_id = CAST(:school_id AS uuid) AND e.work_id IS NOT NULL
"""

LATEST_LABELS = f"""
SELECT DISTINCT ON (ls.work_id) ls.id, ls.work_id, ls.checked, ls.min_age, ls.max_age
FROM labelsets ls JOIN ({HOLDINGS}) held ON held.work_id = ls.work_id
ORDER BY ls.work_id, ls.id DESC
"""


def array_length(path: str) -> str:
    return f"CASE WHEN jsonb_typeof({path}) = 'array' THEN jsonb_array_length({path}) ELSE 0 END"


def engagement_query() -> str:
    feedback = "f.choices"
    counts = {
        key: array_length(f"{feedback} -> '{key}'")
        for key in ("liked", "disliked", "read")
    }
    return f"""
        WITH cohort AS ({COHORT}), milestones AS (
            SELECT h.session_id,
                   bool_or(h.interaction_type = 'MESSAGE') AS reached,
                   bool_or(h.interaction_type = 'INPUT') AS feedback
            FROM conversation_history h JOIN cohort s ON s.id = h.session_id
            WHERE h.content ->> 'input_type' = 'book_feedback'
            GROUP BY h.session_id
        ), latest_feedback AS (
            SELECT DISTINCT ON (h.session_id) h.session_id,
                h.content -> 'validated_feedback' AS choices
            FROM conversation_history h JOIN cohort s ON s.id = h.session_id
            WHERE h.interaction_type = 'INPUT' AND h.content ->> 'input_type' = 'book_feedback'
            ORDER BY h.session_id, h.created_at DESC, h.id DESC
        )
        SELECT date_trunc('week', s.started_at)::date AS week,
               count(*) AS sessions,
               count(*) FILTER (WHERE m.reached) AS reached,
               count(*) FILTER (WHERE m.feedback AND m.reached) AS feedback,
               count(*) FILTER (WHERE m.feedback AND m.reached AND
                   jsonb_typeof(f.choices) IS DISTINCT FROM 'object') AS unverified_feedback,
               coalesce(sum({counts["liked"]}) FILTER (WHERE m.feedback AND m.reached), 0) AS liked,
               coalesce(sum({counts["disliked"]}) FILTER (WHERE m.feedback AND m.reached), 0) AS disliked,
               coalesce(sum({counts["read"]}) FILTER (WHERE m.feedback AND m.reached), 0) AS already_read,
               count(*) FILTER (WHERE m.feedback AND m.reached AND {counts["liked"]} > 0) AS liked_sessions,
               count(*) FILTER (WHERE m.feedback AND m.reached AND {counts["disliked"]} > 0) AS disliked_sessions,
               count(*) FILTER (WHERE m.feedback AND m.reached AND {counts["read"]} > 0) AS read_sessions
        FROM cohort s LEFT JOIN milestones m ON m.session_id = s.id
        LEFT JOIN latest_feedback f ON f.session_id = s.id
        GROUP BY week ORDER BY week
        """


def collection_query() -> str:
    return f"""
        WITH held AS ({HOLDINGS}), latest AS ({LATEST_LABELS})
        SELECT count(*) AS works,
          count(*) FILTER (WHERE
            EXISTS (SELECT 1 FROM labelset_hue_association h WHERE h.labelset_id = l.id)
            AND EXISTS (SELECT 1 FROM labelset_reading_ability_association r WHERE r.labelset_id = l.id)
          ) AS labelled,
          count(*) FILTER (WHERE l.checked IS NOT TRUE) AS awaiting_review,
          count(*) FILTER (WHERE l.min_age IS NULL OR l.max_age IS NULL) AS missing_age,
          (SELECT count(*) FROM collections c
           JOIN collection_items ci ON ci.collection_id = c.id
           LEFT JOIN editions e ON e.isbn = ci.edition_isbn
           WHERE c.school_id = CAST(:school_id AS uuid) AND e.work_id IS NULL) AS unmatched_items
        FROM held LEFT JOIN latest l ON l.work_id = held.work_id
        """


def interests_query() -> str:
    return f"""
        WITH cohort AS ({COHORT}), latest AS ({LATEST_LABELS}), interests AS (
          SELECT h.id, h.name, count(DISTINCT s.id) AS sessions
          FROM cohort s
          CROSS JOIN LATERAL jsonb_array_elements_text(
            CASE WHEN jsonb_typeof(s.state #> '{{user,hue_keys}}') = 'array'
            THEN s.state #> '{{user,hue_keys}}' ELSE '[]'::jsonb END
          ) selected(key)
          JOIN hues h ON h.key = selected.key
          GROUP BY h.id, h.name HAVING count(DISTINCT s.id) >= 5
            AND ((SELECT count(*) FROM cohort) - count(DISTINCT s.id) = 0
              OR (SELECT count(*) FROM cohort) - count(DISTINCT s.id) >= 5)
        )
        SELECT i.name, i.sessions, count(DISTINCT l.work_id) AS labelled_works
        FROM interests i
        LEFT JOIN (latest l JOIN labelset_hue_association a ON l.id = a.labelset_id)
          ON a.hue_id = i.id
        GROUP BY i.id, i.name, i.sessions
        ORDER BY labelled_works, i.sessions DESC, i.name LIMIT 8
        """


async def read_engagement(db: AsyncSession, parameters: dict) -> list[dict]:
    result = await db.execute(text(engagement_query()), parameters)
    return [dict(row) for row in result.mappings()]


async def read_collection(db: AsyncSession, school_id: UUID) -> dict:
    result = await db.execute(text(collection_query()), {"school_id": str(school_id)})
    return dict(result.mappings().one())


async def read_interests(db: AsyncSession, parameters: dict) -> list[dict]:
    result = await db.execute(text(interests_query()), parameters)
    return [dict(row) for row in result.mappings()]


async def read_snapshot(db: AsyncSession, parameters: dict) -> dict:
    await db.execute(
        text(
            "SELECT set_config('statement_timeout', '2s', true), set_config('jit', 'off', true)"
        )
    )
    result = await db.execute(
        text(f"""
        WITH engagement AS ({engagement_query()}),
             collection AS ({collection_query()}),
             interests AS ({interests_query()})
        SELECT coalesce((SELECT jsonb_agg(e ORDER BY e.week) FROM engagement e), '[]'::jsonb) AS engagement,
               (SELECT to_jsonb(c) FROM collection c) AS collection,
               coalesce((SELECT jsonb_agg(i ORDER BY i.labelled_works, i.sessions DESC, i.name) FROM interests i), '[]'::jsonb) AS interests
    """),
        parameters,
    )
    snapshot = dict(result.mappings().one())
    for row in snapshot["engagement"]:
        row["week"] = date.fromisoformat(row["week"])
    return snapshot


def query_parameters(school_id: UUID, start: datetime, end: datetime) -> dict:
    return {"school_id": str(school_id), "start": start, "end": end}

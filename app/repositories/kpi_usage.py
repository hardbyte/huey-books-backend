from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def read_usage(db: AsyncSession, start: datetime, end: datetime) -> list[dict]:
    await db.execute(text("SET LOCAL statement_timeout = '2s'"))
    rows = await db.execute(
        text("""
            WITH cohort AS MATERIALIZED (
                SELECT id, school_id, started_at
                FROM conversation_sessions
                WHERE started_at >= :start AND started_at < :end
            ), reached AS (
                SELECT DISTINCT h.session_id
                FROM conversation_history h JOIN cohort c ON c.id = h.session_id
                WHERE h.interaction_type = 'MESSAGE'
                  AND (h.content ->> 'input_type' = 'book_feedback'
                       OR h.content @> '{"messages":[{"type":"book_list"}]}')
                  AND h.created_at < :end
            )
            SELECT date_trunc('week', c.started_at)::date AS week,
                   count(*) AS sessions,
                   count(r.session_id) AS reached_recommendations,
                   count(DISTINCT c.school_id) FILTER (WHERE r.session_id IS NOT NULL) AS active_sites,
                   count(*) FILTER (WHERE c.school_id IS NULL) AS unattributed_sessions
            FROM cohort c LEFT JOIN reached r ON r.session_id = c.id
            GROUP BY GROUPING SETS ((date_trunc('week', c.started_at)::date), ())
            ORDER BY week NULLS LAST
        """),
        {"start": start, "end": end},
    )
    return [dict(row) for row in rows.mappings()]

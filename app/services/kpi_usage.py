from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.repositories.kpi_usage import read_usage
from app.repositories.outbox import get_outbox_health
from app.schemas.kpis import DeliverySnapshot, UsageCounts, UsageReport, UsageWeek


async def get_delivery_snapshot(db: AsyncSession) -> DeliverySnapshot:
    await db.execute(text("SET LOCAL statement_timeout = '2s'"))
    health = await get_outbox_health(
        db, slack_enabled=get_settings().SLACK_NOTIFICATIONS_ENABLED
    )
    return DeliverySnapshot(
        observed_at=datetime.now(timezone.utc),
        pending_count=health.pending_count,
        disabled_count=health.disabled_count,
        oldest_pending_age_seconds=health.oldest_pending_age_seconds,
    )


async def get_usage(db: AsyncSession, weeks: int) -> UsageReport:
    end = datetime.now(timezone.utc)
    monday = end.date() - timedelta(days=end.weekday())
    start = datetime.combine(monday - timedelta(weeks=weeks - 1), datetime.min.time())
    rows = await read_usage(db, start, end.replace(tzinfo=None))
    totals = next(row for row in rows if row["week"] is None)
    by_week = {row["week"]: row for row in rows if row["week"] is not None}
    points = []
    for index in range(weeks):
        week = start.date() + timedelta(weeks=index)
        counts = by_week.get(week, {})
        points.append(
            UsageWeek(
                week=week,
                partial=week == monday,
                **{key: counts.get(key, 0) for key in UsageCounts.model_fields},
            )
        )
    return UsageReport(
        start=start.replace(tzinfo=timezone.utc),
        end=end,
        totals=UsageCounts(**totals),
        points=points,
    )

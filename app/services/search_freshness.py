from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from app.models import SearchIndexRefresh

logger = get_logger()
FRESHNESS_BUDGET_SECONDS = {
    "recommendable_editions": 1800,
    "search_view_v1": 7200,
    "work_collection_frequency": 7200,
}


def freshness_status(snapshot_at: datetime | None, now: datetime, budget: int) -> dict:
    age = max(0.0, (now - snapshot_at).total_seconds()) if snapshot_at else None
    return {
        "known": age is not None,
        "age_seconds": age,
        "budget_seconds": budget,
        "within_sla": age is not None and age <= budget,
    }


async def check_search_freshness(session: AsyncSession) -> list[dict]:
    now = await session.scalar(select(func.clock_timestamp()))
    snapshots = dict(
        (
            await session.execute(
                select(
                    SearchIndexRefresh.index_name, SearchIndexRefresh.source_snapshot_at
                )
            )
        ).all()
    )
    statuses = []
    for index_name, budget in FRESHNESS_BUDGET_SECONDS.items():
        status = {
            "index_name": index_name,
            **freshness_status(snapshots.get(index_name), now, budget),
        }
        logger.info("Search index freshness", **status)
        statuses.append(status)
    logger.info("Search freshness check completed")
    return statuses

from time import perf_counter

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from app.models import Author, Work
from app.models.author_work_association import author_work_association_table as awa
from app.models.search_view import search_view_v1
from app.models.work_collection_frequency import work_collection_frequency as cf
from app.schemas.author import AuthorBrief
from app.schemas.work import WorkBrief, WorkType

logger = get_logger()


def search_candidates(query_param: str | None = None, author_id: int | None = None):
    """Rank one row per work before response hydration or pagination."""
    frequency = func.greatest(func.coalesce(cf.c.collection_frequency, 0), 0)
    score = frequency
    statement = select(search_view_v1.c.work_id).select_from(
        search_view_v1.join(Work, Work.id == search_view_v1.c.work_id).outerjoin(
            cf, cf.c.work_id == search_view_v1.c.work_id
        )
    )
    if query_param is not None:
        query = func.websearch_to_tsquery("english", query_param)
        score = func.ts_rank(search_view_v1.c.document, query) * (
            1 + func.log(1 + frequency)
        )
        statement = statement.where(search_view_v1.c.document.op("@@")(query))
    if author_id is not None:
        statement = statement.where(
            select(1)
            .where(
                awa.c.work_id == search_view_v1.c.work_id,
                awa.c.author_id == author_id,
            )
            .exists()
        )
    return statement.add_columns(score.label("score"))


async def book_search(
    session: AsyncSession,
    pagination,
    query_param: str | None = None,
    author_id: int | None = None,
) -> list[WorkBrief]:
    started = perf_counter()
    candidates = search_candidates(query_param, author_id).subquery()
    page = (
        select(candidates)
        .order_by(candidates.c.score.desc(), candidates.c.work_id)
        .offset(pagination.skip)
        .limit(pagination.limit)
        .cte("search_page")
    )
    highlight_config = 'StartSel="<b>", StopSel="</b>"'

    def highlighted(column):
        value = func.coalesce(column, "")
        if query_param is None:
            return value
        return func.ts_headline(
            "english",
            value,
            func.websearch_to_tsquery("english", query_param),
            highlight_config,
        )

    statement = (
        select(
            Work.id,
            Work.leading_article,
            highlighted(Work.title).label("title"),
            highlighted(Work.subtitle).label("subtitle"),
        )
        .join(page, page.c.work_id == Work.id)
        .order_by(page.c.score.desc(), page.c.work_id)
    )
    rows = (await session.execute(statement)).all()
    retrieved = perf_counter()
    authors_by_work: dict[int, list[AuthorBrief]] = {row.id: [] for row in rows}
    if rows:
        authors = await session.execute(
            select(
                awa.c.work_id,
                Author.id,
                highlighted(Author.first_name).label("first_name"),
                highlighted(Author.last_name).label("last_name"),
            )
            .select_from(awa.join(Author, Author.id == awa.c.author_id))
            .where(awa.c.work_id.in_(authors_by_work))
            .order_by(awa.c.work_id, Author.id)
        )
        for author in authors:
            authors_by_work[author.work_id].append(
                AuthorBrief(
                    id=str(author.id),
                    first_name=author.first_name,
                    last_name=author.last_name,
                )
            )
    loaded = perf_counter()
    results = [
        WorkBrief(
            id=str(row.id),
            leading_article=row.leading_article,
            type=WorkType.BOOK,
            authors=authors_by_work[row.id],
            title=row.title,
            subtitle=row.subtitle,
        )
        for row in rows
    ]
    logger.info(
        "Search phases",
        retrieval_ms=(retrieved - started) * 1000,
        authors_ms=(loaded - retrieved) * 1000,
        response_ms=(perf_counter() - loaded) * 1000,
    )
    return results


async def update_search_view_v1(session: AsyncSession):
    logger.info("Refreshing search view v1")
    await session.execute(text("SET LOCAL lock_timeout = '5s'"))
    await session.execute(text("SET LOCAL statement_timeout = '120s'"))
    stmt = text("SELECT public.refresh_search_index()")
    await session.execute(stmt)
    await session.commit()
    logger.info("Refreshed search view v1")

from sqlalchemy import String, cast, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from app.models import Author, Work
from app.models.author_work_association import author_work_association_table
from app.models.search_view import search_view_v1
from app.models.series_works_association import series_works_association_table
from app.models.work_collection_frequency import work_collection_frequency
from app.schemas.author import AuthorBrief
from app.schemas.work import WorkBrief, WorkType

logger = get_logger()

awa = author_work_association_table
swa = series_works_association_table
cf = work_collection_frequency


async def book_search(
    session: AsyncSession,
    pagination,
    query_param: str | None = None,
    author_id: int = None,
) -> list[WorkBrief]:
    # Constructing the query
    highlight_config = 'StartSel="<b>", StopSel = "</b>"'

    query_components = [
        Work.id.label("work_id"),
        Work.leading_article.label("leading_article"),
        # If there's no search query, use simple author attributes
        func.json_agg(
            func.json_build_object(
                "id",
                cast(Author.id, String),
                "first_name",
                (
                    Author.first_name
                    if query_param is None
                    else func.ts_headline(
                        "english",
                        func.coalesce(Author.first_name, ""),
                        func.plainto_tsquery("english", query_param),
                        highlight_config,
                    )
                ),
                "last_name",
                (
                    Author.last_name
                    if query_param is None
                    else func.ts_headline(
                        "english",
                        func.coalesce(Author.last_name, ""),
                        func.plainto_tsquery("english", query_param),
                        highlight_config,
                    )
                ),
            )
        ).label("authors"),
        # Add title and subtitle with or without highlighting
        (
            func.coalesce(Work.title, "")
            if query_param is None
            else func.ts_headline(
                "english",
                func.coalesce(Work.title, ""),
                func.plainto_tsquery("english", query_param),
                highlight_config,
            ).label("title")
        ),
        (
            func.coalesce(Work.subtitle, "")
            if query_param is None
            else func.ts_headline(
                "english",
                func.coalesce(Work.subtitle, ""),
                func.plainto_tsquery("english", query_param),
                highlight_config,
            ).label("subtitle")
        ),
    ]

    # Conditionally add rank calculation if there's a search query
    if query_param is not None:
        query_components.append(
            (
                func.ts_rank(
                    search_view_v1.c.document,
                    func.websearch_to_tsquery("english", query_param),
                )
                * func.log(1 + func.coalesce(cf.c.collection_frequency, 0))
            ).label("rank")
        )

    # Time to actually start building a Sqlalchemy query
    stmt = select(*query_components).select_from(
        search_view_v1.join(Work, Work.id == search_view_v1.c.work_id)
        .join(awa, awa.c.work_id == Work.id)
        .join(Author, Author.id == awa.c.author_id)
        .join(cf, cf.c.work_id == Work.id)
    )
    # Filter to include only results where at least one author matches the provided author_id
    if author_id is not None:
        author_subquery = (
            select(1)
            .where((awa.c.work_id == Work.id) & (awa.c.author_id == author_id))
            .exists()
            .correlate(Work)
        )
        stmt = stmt.where(author_subquery)

    # # Apply conditional where clause for search query
    if query_param is not None:
        stmt = stmt.where(
            search_view_v1.c.document.op("@@")(
                func.websearch_to_tsquery("english", query_param)
            )
        )
        # Order by search ranking (if there was a search query)
        stmt = stmt.order_by(text("rank DESC"))
    else:
        # Order by popularity
        stmt = stmt.order_by(cf.c.collection_frequency.desc())

    # Apply group by and ordering
    if query_param is not None:
        stmt = stmt.group_by(
            Work.id, cf.c.collection_frequency, search_view_v1.c.document
        )
    else:
        stmt = stmt.group_by(Work.id, cf.c.collection_frequency)

    # pagination (just limits for now)
    stmt = stmt.offset(pagination.skip).limit(pagination.limit)
    res = (await session.execute(stmt)).fetchall()

    logger.info("Result of search", res=res)

    return [
        WorkBrief(
            id=str(row[0]),
            leading_article=row[1],
            type=WorkType.BOOK,
            authors=[
                AuthorBrief.model_validate(a) if a is not None else None for a in row[2]
            ],
            title=row[3],
            subtitle=row[4],
        )
        for row in res
    ]


async def update_search_view_v1(session: AsyncSession):
    logger.info("Refreshing search view v1")
    stmt = text("SELECT public.refresh_search_index()")
    await session.execute(stmt)
    await session.commit()
    logger.info("Refreshed search view v1")

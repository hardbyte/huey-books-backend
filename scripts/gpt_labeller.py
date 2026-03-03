import asyncio

from openai.error import RateLimitError
from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session, contains_eager
from structlog import get_logger

from app.config import get_settings
from app.crud import booklist as crud_booklist
from app.crud import event as crud_event
from app.db.session import database_connection
from app.models import Edition
from app.models.booklist import BookList
from app.models.booklist_work_association import BookListItem
from app.models.collection import Collection
from app.models.collection_item import CollectionItem
from app.models.labelset import LabelSet
from app.models.work import Work
from app.schemas.booklist import BookListUpdateIn, ItemUpdateType

# from app.services.events import create_event
from app.services.labelling import label_and_update_work

settings = get_settings()
logger = get_logger()

engine, SessionLocal = database_connection(
    settings.SQLALCHEMY_DATABASE_URI,
    pool_size=settings.DATABASE_POOL_SIZE,
    max_overflow=settings.DATABASE_MAX_OVERFLOW,
)

school_ids = [
    "1e698bfa-dcc6-4993-9fce-b4d35c427c47",
    "4b4eb2db-9442-4c6a-bfc4-467c4df439ba",
    "72603041-d2f9-4ae6-8e4c-e2f85d9d101f",
    "7ad69d31-e603-420d-b81d-17a09106718b",
    "df6dc32c-a03d-456a-8ca2-d386ec4a00e2",
    "9f798813-865f-4129-87ca-9ef170015325",
    "09d37f3c-b494-407b-8b2c-572c7985a59f",
    "be63175e-b81e-4f2b-a853-5d9ed0cf0e57",
    "851b23be-7b17-4b59-bd59-2f3765ad753c",
    "b8a80b9e-cd71-409e-8a70-76f758e1d157",
    "b384ffd1-36ad-4fb9-9dfd-4277453ff903",
    "cbe0280b-5350-4a32-b524-ef13043d1db8",
]

failed_list_id = "3f56747b-f008-4394-b571-0ae4f4c1c339"

BACKOFF = 300  # 5 minutes
WAITING = False
WAIT_LOCK = asyncio.Lock()


async def label():
    global WAITING
    global BACKOFF

    while True:
        with Session(engine) as session:
            # select a Work where:
            # its associated labelset has min and max age,
            # its associated lableset is missing reading abilities and/or hues
            # at least one edition has a cover image,
            # the book appears in a collection associated with a top school,
            # order by date published descending
            work = (
                session.query(Work)
                .join(Work.labelset)
                .join(Edition, isouter=False)
                .join(
                    CollectionItem,
                    and_(
                        Edition.isbn == CollectionItem.edition_isbn,
                        CollectionItem.collection_id.in_(
                            select(Collection.id).where(
                                Collection.school_id.in_(school_ids)
                            )
                        ),
                    ),
                    isouter=True,
                )
                .join(Collection)
                .filter(
                    LabelSet.min_age.isnot(None),
                    LabelSet.max_age.isnot(None),
                    or_(
                        ~LabelSet.reading_abilities.any(),
                        ~LabelSet.hues.any(),
                    ),
                    Work.editions.any(Edition.cover_url.isnot(None)),
                    ~exists().where(
                        and_(
                            BookListItem.work_id == Work.id,
                            BookListItem.booklist_id == failed_list_id,
                        )
                    ),
                )
                .order_by(Edition.date_published.desc())
                .options(
                    contains_eager(Work.editions).contains_eager(Edition.collections)
                )
                .limit(1)
                .one()
            )

            # to avoid lazy loading and caching weirdness, we need to expunge the work and re-fetch it
            work_id = work.id
            session.expunge(work)
            work = session.get(Work, work_id)

            try:
                await label_and_update_work(work, session)
                BACKOFF = 300  # reset backoff to 5 minutes if successful
            except RateLimitError:
                async with WAIT_LOCK:
                    WAITING = True
                    logger.warning(
                        f"OpenAI: RateLimitError. All threads will wait {BACKOFF} seconds."
                    )
                    await asyncio.sleep(BACKOFF)
                    BACKOFF *= 2  # increase backoff exponentially for each retry
                    WAITING = False
            except ValueError as e:
                crud_event.create(
                    session,
                    "GPT Labeling Failed",
                    f"Failed to label {work.title}.",
                    {"work_id": work.id, "error": str(e)},
                )

                failed_list = session.query(BookList).get(failed_list_id)
                update = BookListUpdateIn(
                    items=[{"work_id": work.id, "action": ItemUpdateType.ADD}]
                )
                crud_booklist.update(db=session, db_obj=failed_list, obj_in=update)

                logger.warning(f"Failed to label {work.title}. Skipping...")
                continue

        await asyncio.sleep(1)


async def main():
    tasks = []
    for _ in range(3):
        tasks.append(asyncio.create_task(label()))

    while True:
        if WAITING:
            await asyncio.sleep(1)
        else:
            tasks.append(asyncio.create_task(label()))
            await asyncio.sleep(20)

        if len(tasks) == 0:
            break

    await asyncio.gather(*tasks)


asyncio.run(main())

# to get the number of works that need to be labeled:
# with Session(engine) as session:
#   work_count = (
#     session.query(func.count(distinct(Work.id)))
#     .join(Work.labelset)
#     .join(Edition)
#     .join(
#         CollectionItem,
#         and_(
#             Edition.isbn == CollectionItem.edition_isbn,
#             CollectionItem.collection_id.in_(
#                 select(Collection.id).where(Collection.school_id.in_(school_ids))
#             ),
#         ),
#         isouter=True,
#     )
#     .join(Collection)
#     .filter(
#         LabelSet.min_age.isnot(None),
#         LabelSet.max_age.isnot(None),
#         or_(
#             ~LabelSet.reading_abilities.any(),
#             ~LabelSet.hues.any(),
#         ),
#         Work.editions.any(Edition.cover_url.isnot(None)),
#     )
#     .one()[0]
#   )
#   print(work_count)

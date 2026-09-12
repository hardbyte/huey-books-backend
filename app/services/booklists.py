from sqlalchemy.orm import Session
from structlog import get_logger

import app.services as services
from app import crud
from app.config import get_settings
from app.crud.base import deep_merge_dicts
from app.models.booklist import BookList, ListSharingType, ListType
from app.models.event import EventLevel, EventSlackChannel
from app.repositories.booklist_repository import booklist_repository
from app.schemas.booklist import (
    AlternativeBookCover,
    BookListCreateIn,
    BookListDetail,
    BookListDetailEnriched,
    BookListItemCreateIn,
    BookListItemEnriched,
    BookListItemInfo,
    BookListUpdateIn,
)
from app.schemas.edition import EditionDetail
from app.schemas.pagination import Pagination
from app.schemas.users.huey_attributes import HueyAttributes
from app.services.background_tasks import queue_background_task
from app.services.browser_observations import create_cover_receipt
from app.services.editions import get_definitive_isbn

# Local import to avoid circular dependency
# from app.services.events import create_event
from app.services.gcp_storage import (
    base64_string_to_bucket,
    delete_blob,
    url_to_blob_name,
)

logger = get_logger()
settings = get_settings()


async def generate_reading_pathway_lists(
    user_id: str, attributes: HueyAttributes, limit: int = 100, commit: bool = True
):
    """
    Generate booklists `Books to read now` and `Books to read next` for the provided user,
    populating each with `limit` appropriate books based on the provided `huey_attributes`
    """

    from app.db.session import get_async_session_maker

    logger.info(
        "Creating reading pathway booklists for user",
        user_id=user_id,
        attributes=attributes,
        limit=limit,
    )

    AsyncSessionMaker = get_async_session_maker()
    async with AsyncSessionMaker() as session:
        try:
            current_reading_ability = attributes.reading_ability[0]
        except (ValueError, TypeError, IndexError):
            logger.warning(
                "Attempt to create reading pathway when no Huey attributes were provided",
                user_id=user_id,
            )
            return

        # Get the read now list by generating 10 books via standard recommendation
        current_reading_ability_key = current_reading_ability.name
        read_now_query = await services.recommendations.get_recommended_labelset_query(
            session,
            hues=attributes.hues,
            age=attributes.age,
            reading_abilities=[current_reading_ability_key],
        )

        # Get the read next list by incrementing the reading ability and doing the same
        next_reading_ability_key = services.recommendations.gen_next_reading_ability(
            current_reading_ability
        ).name
        read_next_query = await services.recommendations.get_recommended_labelset_query(
            session,
            hues=attributes.hues,
            age=attributes.age,
            reading_abilities=[next_reading_ability_key],
        )

        now_results = (await session.execute(read_now_query.limit(limit))).all()
        items_to_read_now = [
            BookListItemCreateIn(
                work_id=work.id,
                info=BookListItemInfo(
                    edition=edition.isbn,
                ),
            )
            for (work, edition, _labelset) in now_results
        ]
        read_now_booklist_data = BookListCreateIn(
            name="Books To Read Now",
            type=ListType.PERSONAL,
            user_id=str(user_id),
            items=items_to_read_now,
            info={"description": "A collection of books to enjoy today"},
        )

        next_results = (await session.execute(read_next_query.limit(limit))).all()
        items_to_read_next = [
            BookListItemCreateIn(
                work_id=work.id,
                info=BookListItemInfo(
                    edition=edition.isbn,
                ),
            )
            for (work, edition, _labelset) in next_results
        ]
        read_next_booklist_data = BookListCreateIn(
            name="Books To Read Next",
            type=ListType.PERSONAL,
            user_id=str(user_id),
            items=items_to_read_next,
            info={
                "description": "A collection of books to enjoy tomorrow, or challenge your reading"
            },
        )

        read_now_orm = await booklist_repository.acreate(
            session, obj_in=read_now_booklist_data, commit=commit
        )
        read_next_orm = await booklist_repository.acreate(
            session, obj_in=read_next_booklist_data, commit=commit
        )

        await crud.event.acreate(
            session,
            title="Created reading pathway lists",
            description="Created Books To Read Now and Books To Read Later",
            account=await crud.user.aget(session, user_id),
            commit=commit,
            info={
                "attributes": attributes.model_dump(),
                "read_now_count": len(items_to_read_now),
                "read_next_count": len(items_to_read_next),
            },
        )

        return read_now_orm, read_next_orm


def generate_reading_pathway_lists_sync(
    user_id: str, attributes: HueyAttributes, limit: int = 100
):
    """
    Synchronous wrapper that queues reading pathway list generation as a background task.
    Use this for synchronous contexts where async is not available.
    """
    logger.info(
        "Queueing reading pathway booklist generation",
        user_id=user_id,
        attributes=attributes.dict(),
        limit=limit,
    )

    payload = {
        "user_id": user_id,
        "attributes": attributes.dict(),
        "limit": limit,
    }

    return queue_background_task("generate-reading-pathways", payload)


def _handle_upload_booklist_feature_image(
    image_data: str,
    booklist_id: str,
) -> str:
    """
    Handle a feature image upload for a public booklist.
    """
    # generate folder and filename
    folder = "booklist-feature-images"
    filename = booklist_id

    # upload the image to the bucket
    public_url = base64_string_to_bucket(
        data=image_data,
        folder=folder,
        filename=filename,
        bucket_name=settings.GCP_HUEY_MEDIA_BUCKET,
    )

    return public_url


def handle_booklist_feature_image_update(
    booklist: BookList, image_data: str
) -> str | None:
    """
    Handle a feature image update for an existing booklist.
    If image_data is empty, purges any existing image from gcp and the db object.
    If the image is new, deletes the old image from gcp.
    """
    new_url = (
        _handle_upload_booklist_feature_image(
            image_data,
            str(booklist.id),
        )
        if image_data
        else None
    )
    current_url = booklist.info.get("image_url")
    if current_url and current_url != new_url:
        delete_blob(
            settings.GCP_HUEY_MEDIA_BUCKET,
            url_to_blob_name(current_url),
        )
    return new_url


def handle_new_booklist_feature_image(booklist_id: str, image_data: str) -> str | None:
    """
    Handle a feature image upload for a new booklist.
    """
    return _handle_upload_booklist_feature_image(image_data, booklist_id)


def validate_booklist_publicity(
    new_data: BookListUpdateIn | BookListCreateIn, old_data: BookList = None
) -> None:
    new_data_dict = new_data.dict(exclude_unset=True)
    old_data_dict = (
        {
            "slug": old_data.slug,
            "type": old_data.type,
            "sharing": old_data.sharing,
        }
        if old_data
        else {}
    )
    deep_merge_dicts(new_data_dict, old_data_dict)

    slug = new_data_dict.get("slug")
    list_type = new_data_dict.get("type")
    sharing = new_data_dict.get("sharing")

    is_public_huey_list = (
        list_type == ListType.HUEY and sharing == ListSharingType.PUBLIC
    )

    if slug and list_type != ListType.HUEY:
        raise ValueError("A slug can only be provided for a Huey list")
    if is_public_huey_list and not slug:
        raise ValueError("A slug must be provided for a Public Huey list")


def populate_booklist_object(
    booklist: BookList,
    session: Session,
    pagination,
    enriched: bool = False,
) -> BookListDetail | BookListDetailEnriched:
    logger.debug("Getting booklist", booklist=booklist)
    booklist_items = booklist_repository.get_detail_items(
        session, booklist.id, skip=pagination.skip, limit=pagination.limit
    )
    missing_edition_events = []

    def get_enriched_booklist_items() -> list[BookListItemEnriched]:
        enriched_booklist_items = []
        requested_isbns: dict[int, str] = {}
        for item in booklist_items:
            try:
                requested_isbns[item.work_id] = get_definitive_isbn(
                    (item.info or {}).get("edition")
                )
            except (AssertionError, ValueError, TypeError):
                continue
        editions = booklist_repository.get_detail_editions(
            session, [item.work_id for item in booklist_items], requested_isbns
        )
        alternative_covers = booklist_repository.get_alternative_covers(
            session,
            [
                edition.work_id
                for edition in editions.values()
                if not (edition.cover_url or "").strip()
            ],
        )
        for item in booklist_items:
            edition = editions.get(item.work_id)
            if edition is None:
                missing_edition_events.append(
                    {
                        "description": f"The booklist '{booklist.name}' has an item referencing work {item.work.title} which has no editions",
                        "info": {
                            "booklist_id": booklist.id,
                            "booklist_name": booklist.name,
                            "work_id": item.work_id,
                            "work_title": item.work.title,
                        },
                    }
                )
                # Skip this item
                continue

            edition_detail = EditionDetail.model_validate(edition)
            alternative_cover = None
            if not edition_detail.cover_url and edition.work_id in alternative_covers:
                source_isbn, url = alternative_covers[edition.work_id]
                alternative_cover = AlternativeBookCover(
                    url=url,
                    source_isbn=source_isbn,
                )
            enriched_item = BookListItemEnriched(
                **item.__dict__,
                edition=edition_detail,
                alternative_cover=alternative_cover,
                observation_receipt=create_cover_receipt(
                    "exact"
                    if edition_detail.cover_url
                    else "alternative"
                    if alternative_cover
                    else "placeholder",
                    settings.SECRET_KEY,
                )
                if settings.BROWSER_OBSERVATIONS_ENABLED
                else None,
            )
            enriched_booklist_items.append(enriched_item)

        return enriched_booklist_items

    if enriched:
        booklist_items = get_enriched_booklist_items()

    logger.debug("Returning paginated booklist", item_count=len(booklist_items))
    booklist.data = booklist_items
    booklist.pagination = Pagination(**pagination.to_dict(), total=booklist.book_count)

    response = (
        BookListDetail.model_validate(booklist)
        if not enriched
        else BookListDetailEnriched.model_validate(booklist)
    )
    if missing_edition_events:
        from app.services.events import create_event

        for warning in missing_edition_events:
            create_event(
                session=session,
                level=EventLevel.WARNING,
                title="Work referenced by a booklist has no editions",
                description=warning["description"],
                info=warning["info"],
                slack_channel=EventSlackChannel.EDITORIAL,
            )
    return response

import json
import xml.etree.ElementTree as ET
from datetime import datetime

import isbnlib
import requests
from google.api_core.exceptions import NotFound
from structlog import get_logger

from app.config import get_settings
from app.models.event import EventLevel
from app.models.work import WorkType
from app.repositories.author_repository import author_repository
from app.repositories.edition_repository import edition_repository
from app.repositories.labelset_repository import labelset_repository
from app.repositories.work_repository import work_repository
from app.schemas.author import AuthorCreateIn
from app.schemas.hydration import HydratedBookData
from app.schemas.labelset import LabelSetCreateIn
from app.schemas.work import WorkCreateIn
from app.services.background_tasks import queue_background_task
from app.services.cover_images import handle_new_edition_cover_image
from app.services.editions import create_missing_editions, get_definitive_isbn
from app.services.events import create_event
from app.services.gcp_storage import (
    get_blob,
    get_first_blob_by_prefix,
    img_url_to_b64_string,
)
from app.services.util import chunks

logger = get_logger()
settings = get_settings()


class NielsenException(Exception):
    """Raise when the API query doesn't go entirely according to plan"""

    msg = "Something went wrong with the Nielsen API."


class NielsenServiceException(NielsenException):
    """Raise when the API service is unavailable"""

    msg = "The Nielsen API is currently unreachable."


class NielsenAuthException(NielsenException):
    """Raise when the API rejects authorization"""

    msg = "Invalid Nielsen API credentials."


class NielsenRateException(NielsenException):
    """Raise when the API rate limit has been exceeded"""

    msg = "Exceeded daily Nielsen API rate."


class NielsenNoResultsException(NielsenException):
    """Raise when the API returns no results for a query"""

    msg = "No results for the provdided Nielsen query."


def check_result(isbn, result_code, hits):
    if result_code == "00":
        if (hits is None) or (hits is not None and int(hits.text) < 1):
            raise NielsenNoResultsException(f"Nielsen found no results for isbn {isbn}")

    elif result_code == "01":
        raise NielsenServiceException("Nielsen service is unavailable")

    elif result_code == "02":
        raise NielsenAuthException("Nielsen rejected the credentials")

    elif result_code == "03":
        raise NielsenException(
            "Nielsen encountered a server error when querying " + isbn
        )

    elif result_code == "50":
        raise NielsenRateException("Nielsen rate limit reached.")

    else:
        raise NielsenException(
            "Something went wrong when querying Nielsen with the isbn " + isbn
        )


def data_query(isbn) -> dict:
    # retrieve all the book data for a given isbn, in xml format
    response = requests.get(
        settings.NIELSEN_API_URL,
        params={
            "clientId": settings.NIELSEN_CLIENT_ID,
            "password": settings.NIELSEN_PASSWORD,
            "from": 0,
            "to": 1,
            "indexType": 0,  # 0: "Main Book Database"
            "format": 7,  # 7: "XML"
            "resultView": 2,  # 2: "Long" Result View
            "field0": 1,  # 1: Providing an ISBN
            "value0": isbn,
        },
        timeout=30,
    )
    # extract the metadata tag from the arbitrary place it finds itself,
    # then plonk it back at the start where it should be, before tokenising
    raw_response_content = (
        '<?xml version="1.0" encoding="ISO-8859-1"?>'
        + response.content.decode("UTF-8").replace(
            '<?xml version="1.0" encoding="ISO-8859-1"?>', ""
        )
    )
    xml_tree = ET.fromstring(raw_response_content)

    result_code = xml_tree.find("resultCode").text
    hits = xml_tree.find("hits")
    check_result(isbn, result_code, hits)

    data = xml_tree.find("data/data/record")
    # convert to dict
    result = {e.tag: e.text for e in data}

    return result


def image_query(isbn: str, retries: int = 1):
    params = {
        "clientId": settings.NIELSEN_CLIENT_ID,
        "password": settings.NIELSEN_PASSWORD,
        "from": 0,
        "to": 1,
        "indexType": 2,  # 2: "Large Images"
        "format": 7,  # 7: "XML"
        "resultView": 2,  # 2: "Long" Result View
        "field0": 1,  # 1: Providing an ISBN
        "value0": isbn,
    }
    timeout = 30
    for _retry in range(retries):
        response = requests.get(
            settings.NIELSEN_API_URL, params=params, timeout=timeout
        )
        raw_response_content = response.content.decode("UTF-8")
        xml_tree = ET.fromstring(raw_response_content)

        try:
            result_code = xml_tree.find("resultCode").text
            hits = xml_tree.find("hits")
            check_result(isbn, result_code, hits)

            result = xml_tree.find("data").text
            # add padding since nielsen doesn't
            return result + "=="

        except Exception as ex:
            logger.warning(f"{isbn} xml parsing error")
            logger.warning(ex)

    return None


def get_nielsen_data(isbn, use_cache=True):
    try:
        isbn = get_definitive_isbn(isbn)
    except AssertionError:
        logger.warning(f"Invalid ISBN {isbn}. Skipping...")
        return

    if use_cache:
        try:
            data_blob = get_blob(settings.GCP_BOOK_DATA_BUCKET, f"nielsen/{isbn}.json")
            # download the raw data from gcp storage as json, converting to dict
            return json.loads(data_blob.download_as_string().decode("utf-8"))
        except NotFound:
            pass  # cache miss, continue to make the request

    # populate object with the nielsen data
    return data_query(isbn)


def save_editions(
    session, hydrated_book_data: list[HydratedBookData], queue_labelling: bool = True
):
    for book_data in hydrated_book_data:
        isbn = book_data.isbn
        # Get the edition (should exist), work (?), and labelset
        edition = edition_repository.get(session, id=isbn)
        edition.edition_title = book_data.title

        if edition.info is None and book_data.info is not None:
            edition.info = book_data.info.dict()

        if edition.date_published is None and book_data.date_published is not None:
            edition.date_published = book_data.date_published

        if book_data.cover_url:
            edition.cover_url = book_data.cover_url

        edition.hydrated_at = datetime.utcnow()
        session.flush()

        work = edition.work
        if work is None:
            work_data_in = WorkCreateIn(
                type=WorkType.BOOK,
                leading_article=book_data.leading_article,
                title=book_data.title,
                subtitle=book_data.subtitle,
                authors=[],  # not used in this case
                series_name=book_data.series_name,
                series_number=book_data.series_number,
            )
            authors = [
                author_repository.get_or_create(
                    session, AuthorCreateIn.model_validate(a)
                )
                for a in book_data.authors
            ]

            work = work_repository.get_or_create(
                session, work_data=work_data_in, authors=authors
            )
            logger.info("Created new work", work_id=work.id)
            edition.work = work

        labelset = labelset_repository.get_or_create(session, work=work)
        estimated_labelset = book_data.labelset
        logger.info("Estimated labelset", data=estimated_labelset)

        # Update the labelset with the estimated labelset
        labelset_patch = LabelSetCreateIn.model_validate(estimated_labelset)
        logger.info("Patching labelset", labelset_id=labelset.id, data=labelset_patch)
        labelset = labelset_repository.patch(
            session,
            labelset=labelset,
            data=labelset_patch,
        )
        if queue_labelling and book_data.cover_url:
            queue_background_task(
                "generate-labels",
                {"work_id": work.id},
            )


def hydrate(isbn: str, use_cache: bool = True) -> HydratedBookData:
    """
    Get Nielsen data for a given ISBN.
    If use_cache is True, will first check the book data gcp bucket for a cached version.
    If use_cache is False, will make a request to Nielsen's API, updating the cache with the result (unless
    NIELSEN_CACHE_RESULTS is False).
    """
    isbn = get_definitive_isbn(isbn)

    # populate object with nielsen data
    raw_data = get_nielsen_data(isbn, use_cache=use_cache)

    # save the raw data to gcp storage/cache
    if settings.NIELSEN_CACHE_RESULTS:
        blob = get_blob(
            settings.GCP_BOOK_DATA_BUCKET, f"nielsen/{isbn}.json", create=True
        )
        blob.upload_from_string(
            data=json.dumps(raw_data), content_type="application/json"
        )

    book_data = HydratedBookData.from_nielsen_blob(raw_data)

    # apply business logic rules to extrapolate possible labelset fields
    book_data.generate_inferred_labelset()

    # ------grab, store, and reference the cover image (if available)------
    cover_url = None

    # start with Nielsen
    if book_data.info.image_flag:
        if use_cache:
            try:
                existing_blob = get_first_blob_by_prefix(
                    settings.GCP_IMAGE_BUCKET, f"nielsen/{isbn}"
                )
                cover_url = existing_blob.public_url
            except NotFound:
                pass

        if not cover_url:
            if image_data := image_query(isbn, retries=2):
                cover_url = handle_new_edition_cover_image(isbn, image_data, "nielsen")

    # fallback to OpenLibrary
    else:
        if use_cache:
            try:
                existing_blob = get_first_blob_by_prefix(
                    settings.GCP_IMAGE_BUCKET, f"open/{isbn}"
                )
                cover_url = existing_blob.public_url
            except NotFound:
                pass

        api_url = f"https://covers.openlibrary.org/b/isbn/{isbn}-L.jpg"
        if not cover_url and requests.get(api_url).status_code == 200:
            if image_data := img_url_to_b64_string(api_url):
                cover_url = handle_new_edition_cover_image(isbn, image_data, "open")

    book_data.cover_url = cover_url
    # --------------------------------------------

    # grab "other" isbns
    other_isbns = list(set(isbnlib.editions(isbn)) - {isbn})
    book_data.other_isbns = other_isbns

    book_data.hydrated_on = datetime.utcnow()

    return book_data


async def hydrate_bulk(session, isbns_to_hydrate: list[str] = []):
    if len(isbns_to_hydrate) < 1:
        logger.info("No editions to hydrate today. Exiting...")
        return

    # --------------------- begin ---------------------

    total = len(isbns_to_hydrate)
    current, hydrated, with_labelset, errors, not_found = 0, 0, 0, 0, 0
    logger.info("Beginning hydration of " + str(total) + " editions...")
    create_event(
        session,
        "Hydration: Begin batch",
        f"Hydration service has begun hydration of {len(isbns_to_hydrate)} editions.",
        level=EventLevel.DEBUG,
    )

    # using chunks to be nice to the db re: bulk edition creation
    for chunk in chunks(isbns_to_hydrate, 10):
        book_batch: list[HydratedBookData] = []

        for isbn in chunk:
            current += 1

            try:
                isbn = get_definitive_isbn(isbn)
            except Exception:
                logger.warning(f"Invalid ISBN {isbn}. Skipping...")
                continue

            try:
                book_data = hydrate(isbn, use_cache=settings.NIELSEN_ENABLE_CACHE)

            except NielsenServiceException:
                errors += 1
                logger.warning(
                    "Nielsen API service is apparently unavailable. Posting final batch then stopping..."
                )
                save_editions(
                    session, book_batch, queue_labelling=settings.LABEL_AFTER_HYDRATION
                )
                create_event(
                    "Hydration: Nielsen API error",
                    "Hydration service can't access the Nielsen API",
                    level=EventLevel.WARNING,
                )
                return
            except NielsenRateException:
                errors += 1
                logger.warning(
                    "Nielsen Rate Limit reached. Posting final batch then stopping..."
                )
                save_editions(
                    session, book_batch, queue_labelling=settings.LABEL_AFTER_HYDRATION
                )
                create_event(
                    "Hydration: Nielsen rate limit error",
                    "Hydration service hit the Nielsen API rate limit",
                    level=EventLevel.WARNING,
                )
                return
            except NielsenNoResultsException:
                not_found += 1
                logger.warning(f"No results found for {isbn}. Skipping")
                # return the book to the Wriveted API so it knows not to produce it as a candidate again
                book_data.hydrated_on = datetime.utcnow()
                book_batch.append(book_data)
                continue
            except NielsenException as e:
                errors += 1
                logger.warning(
                    "Something unexpected happened with the Nielsen query. Skipping...",
                    exec_info=e,
                )
                continue
            # except Exception as e:
            #     errors += 1
            #     logger.warning("Something else went wrong during Hydration. Skipping...", exec_info=e)
            #     continue

            hydrated += 1

            image_log = ""
            if book_data.cover_url:
                if "nielsen" in book_data.cover_url:
                    image_log = "(+ Nielsen image)"
                elif "open" in book_data.cover_url:
                    image_log = "(+ OpenLibrary image)"

            logger.info(
                f"Hydrated {current} / {total} ({int((current / total) * 100)} %) (Batch size: {len(book_batch)}) {image_log or ''} {'(+labelset)'}",
                isbn=isbn,
            )
            book_batch.append(book_data)

        logger.info(
            f"Have hydrated a partial batch of {len(chunk)}. Posting to Wriveted..."
        )

        await create_missing_editions(session, new_edition_data=book_batch)
        save_editions(
            session, book_batch, queue_labelling=settings.LABEL_AFTER_HYDRATION
        )

    logger.info(
        f"------- Done! Delivered {current} hydrated editions. Goodbye. -------"
    )
    create_event(
        session,
        "Hydration: Finish",
        f"Hydration service has completed, enriching {hydrated} / {total} editions.",
        {
            "total": total,
            "processed": current,
            "hydrated": hydrated,
            "with_labelset": with_labelset,
            "errors": errors,
            "combined_editions": len([len(e.other_isbns) for e in book_batch]),
            "not_found": not_found,
        },
        EventLevel.DEBUG,
    )

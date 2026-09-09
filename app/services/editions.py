from random import randint
from typing import List

from sqlalchemy import and_, select
from sqlalchemy.orm import Session
from structlog import get_logger

from app.config import get_settings
from app.models import Edition
from app.schemas.edition import EditionCreateIn

logger = get_logger()
settings = get_settings()


async def compare_known_editions(session, isbn_list: List[str]):
    from app.repositories.edition_repository import edition_repository

    valid_isbns = clean_isbns(isbn_list)
    known_matches: list[Edition] = (
        session.execute(edition_repository.get_multi_query(db=session, ids=valid_isbns))
        .scalars()
        .all()
    )
    fully_tagged_matches = 0
    for e in known_matches:
        try:
            if e.work.labelset.checked is True:
                fully_tagged_matches += 1
        except Exception as e:
            # print(e)
            continue

    return len(valid_isbns), len(known_matches), fully_tagged_matches


async def create_missing_editions(session, new_edition_data: list[EditionCreateIn]):
    from app.repositories.edition_repository import edition_repository

    isbns = set()
    for edition in new_edition_data:
        try:
            clean = get_definitive_isbn(edition.isbn)
            edition.isbn = clean
            isbns.add(clean)
        except Exception:
            logger.warning("Invalid isbn provided: " + edition.isbn)
            continue

    existing_isbns = (
        session.execute(
            select(Edition.isbn).where(
                and_((Edition.isbn).in_(isbns), (Edition.hydrated is True))
            )
        )
        .scalars()
        .all()
    )
    isbns_to_create = isbns.difference(existing_isbns)

    new_editions_hydrated = []
    new_editions_unhydrated = []

    for edition in new_edition_data:
        if edition.isbn in isbns_to_create:
            if edition.hydrated:
                new_editions_hydrated.append(edition)
            else:
                new_editions_unhydrated.append(edition.isbn)

    if len(new_editions_hydrated) > 0:
        logger.info(
            f"Will have to create {len(new_editions_hydrated)} new hydrated editions"
        )
        edition_repository.create_in_bulk(
            session, bulk_edition_data=new_editions_hydrated
        )
        logger.info("Created new hydrated editions")

    if len(new_editions_unhydrated) > 0:
        logger.info(
            f"Will have to create {len(new_editions_unhydrated)} new unhydrated editions"
        )
        await edition_repository.create_in_bulk_unhydrated(
            session, isbn_list=new_editions_unhydrated
        )
        logger.info("Created new unhydrated editions")

    return (
        isbns,
        len(new_editions_hydrated) + len(new_editions_unhydrated),
        existing_isbns,
    )


async def create_missing_editions_unhydrated(session: Session, isbn_list: list[str]):
    from app.repositories.edition_repository import edition_repository

    final_primary_keys = await edition_repository.create_in_bulk_unhydrated(
        session, isbn_list=isbn_list
    )
    return final_primary_keys


# http://www.niso.org/niso-io/2020/01/new-year-new-isbn-prefix
# https://www.isbn.org/about_isbn_standard
# https://bisg.org/news/479346/New-979-ISBN-Prefixes-Expected-in-2020.htm
# It seems that any ISBN10 has an equivalent ISBN13(978) and vice versa, but also:
# no ISBN10 has an equivalent ISBN13(979), and vice versa.
# Since *every* ISBN is representable as ISBN13, but not *every* ISBN is representable as ISBN10,
# all Editions should be stored by ISBN13, and any queries should standardise the request into
# a "definitive" isbn to store or lookup.
def get_definitive_isbn(isbn: str):
    assert isbn

    # strip all characters that aren't "valid" (i.e. hyphens, spaces)
    valid_chars = ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "X"]
    cleaned_isbn = "".join([i for i in isbn.upper() if i in valid_chars])

    assert len(cleaned_isbn) > 0

    # remove cheeky malformed isbns with valid chars i.e.'000000X79'
    # by ensuring that an X can only appear at the end of an isbn
    assert "X" not in cleaned_isbn or cleaned_isbn.find("X") == len(cleaned_isbn) - 1

    # append leading zeroes to make at least 10 chars
    # (sometimes leading zeroes can be stripped from valid isbn's by excel or the like)
    cleaned_isbn = cleaned_isbn.zfill(10)

    assert isbn_is_valid(cleaned_isbn)

    if len(cleaned_isbn) == 10:
        return convert_10_to_13(cleaned_isbn)
    elif len(cleaned_isbn) == 13:
        return cleaned_isbn


def clean_isbns(isbns: list[str]) -> set[str]:
    cleaned_isbns = set()
    for isbn in isbns:
        try:
            cleaned_isbns.add(get_definitive_isbn(isbn))
        except Exception:
            continue
    return cleaned_isbns


# --- courtesy of https://code.activestate.com/recipes/498104-isbn-13-converter/ ---


def isbn_is_valid(isbn):
    if len(isbn) not in [10, 13]:
        return False

    body = isbn[:-1]
    check = isbn[-1]

    return (
        (check_digit_10(body) == check)
        if len(isbn) == 10
        else (check_digit_13(body) == check)
    )


def check_digit_10(isbn):
    assert len(isbn) == 9
    sum = 0
    for i in range(len(isbn)):
        c = int(isbn[i])
        w = i + 1
        sum += w * c
    r = sum % 11
    if r == 10:
        return "X"
    else:
        return str(r)


def check_digit_13(isbn):
    assert len(isbn) == 12
    sum = 0
    for i in range(len(isbn)):
        c = int(isbn[i])
        if i % 2:
            w = 3
        else:
            w = 1
        sum += w * c
    r = 10 - (sum % 10)
    if r == 10:
        return "0"
    else:
        return str(r)


def convert_10_to_13(isbn):
    assert len(isbn) == 10
    prefix = "978" + isbn[:-1]
    check = check_digit_13(prefix)
    return prefix + check


#  ---------------------------------------------------------------------------------


def generate_random_valid_isbn13():
    base = "978" + str(randint(0, 999999999)).zfill(9)
    return base + check_digit_13(base)

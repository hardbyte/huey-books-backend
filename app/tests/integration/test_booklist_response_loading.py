from uuid import uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.api.common.pagination import PaginatedQueryParams
from app.models import Author, BookList, Edition, Illustrator, LabelSet, Work
from app.models.booklist import ListSharingType, ListType
from app.models.booklist_work_association import BookListItem
from app.models.labelset_hue_association import LabelSetHue, Ordinal
from app.models.labelset_reading_ability_association import LabelSetReadingAbility
from app.repositories.booklist_repository import booklist_repository
from app.services.booklists import populate_booklist_object
from app.services.editions import generate_random_valid_isbn13


@pytest.fixture
def detail_fixture(session):
    marker = uuid4().hex
    author = Author(first_name="Fixture", last_name=marker)
    illustrator = Illustrator(first_name="Picture", last_name=marker)
    foreign_author = Author(first_name="Foreign author", last_name=marker)
    foreign_illustrator = Illustrator(first_name="Foreign picture", last_name=marker)
    booklist = BookList(
        name=marker, type=ListType.HUEY, sharing=ListSharingType.PUBLIC, slug=marker
    )
    session.add_all(
        [author, illustrator, foreign_author, foreign_illustrator, booklist]
    )
    works, editions, labels, items = [], [], [], []
    for index in range(12):
        work = Work(
            title=f"{marker} work {index}",
            authors=[foreign_author if index == 1 else author],
        )
        session.add(work)
        session.flush()
        labelset = LabelSet(
            work_id=work.id,
            min_age=5,
            max_age=12,
            huey_summary=f"Summary {index}",
            checked=True,
        )
        session.add(labelset)
        session.flush()
        session.add_all(
            [
                LabelSetHue(labelset_id=labelset.id, hue_id=1, ordinal=Ordinal.PRIMARY),
                LabelSetReadingAbility(labelset_id=labelset.id, reading_ability_id=1),
            ]
        )
        pair = (
            [
                Edition(
                    isbn=generate_random_valid_isbn13(),
                    work_id=work.id,
                    edition_title=f"Edition {index}/{n}",
                    date_published=2000 + n,
                    cover_url="https://example.com/cover.jpg",
                    illustrators=[foreign_illustrator if index == 1 else illustrator],
                )
                for n in (0, 1)
            ]
            if index < 11
            else []
        )
        session.add_all(pair)
        info = (
            {"edition": pair[0].isbn, "note": "Pinned edition", "feedback": "GOOD"}
            if index % 2 == 0
            else {"note": "Only a note"}
        )
        if index == 11:
            info = None
        item = BookListItem(
            booklist_id=booklist.id, work_id=work.id, order_id=index, info=info
        )
        session.add(item)
        works.append(work)
        editions.append(pair)
        labels.append(labelset)
        items.append(item)
    session.commit()
    data = {
        "id": booklist.id,
        "works": [work.id for work in works],
        "isbns": [[edition.isbn for edition in pair] for pair in editions],
        "illustrator": illustrator.id,
        "author": str(author.id),
        "foreign_author": str(foreign_author.id),
        "foreign_illustrator": foreign_illustrator.id,
        "items": items,
    }
    yield data
    session.delete(booklist)
    session.flush()
    for labelset in labels:
        session.delete(labelset)
    for pair in editions:
        for edition in pair:
            session.delete(edition)
    session.flush()
    for work in works:
        session.delete(work)
    session.flush()
    session.delete(illustrator)
    session.delete(author)
    session.delete(foreign_illustrator)
    session.delete(foreign_author)
    session.commit()


@pytest.mark.parametrize("enriched", [False, True])
@pytest.mark.parametrize("limit", [2, 8])
def test_detail_page_uses_bounded_response_queries(
    session, detail_fixture, enriched, limit
):
    statements = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(session.bind, "after_cursor_execute", capture)
    try:
        with Session(session.bind) as cold:
            booklist = booklist_repository.get_or_404(cold, detail_fixture["id"])
            response = populate_booklist_object(
                booklist, cold, PaginatedQueryParams(skip=2, limit=limit), enriched
            )
            result = response.model_dump(mode="json")
            assert result["pagination"] == {
                "skip": 2,
                "limit": limit,
                "total": 12,
                "page": 2 // limit + 1,
            }
            assert [item["work_id"] for item in result["data"]] == detail_fixture[
                "works"
            ][2 : 2 + limit]
            assert len(statements) <= (12 if enriched else 6)
            assert all(
                "count(collection_items.id)" not in statement
                for statement in statements
            )
            for index, item in enumerate(result["data"], start=2):
                assert item["work"]["labelset"]["hues"]
                assert item["work"]["labelset"]["reading_abilities"]
                assert (
                    cold.get(LabelSet, int(item["work"]["labelset"]["id"])).checked
                    is True
                )
                assert item["info"]["note"] == (
                    "Pinned edition" if index % 2 == 0 else "Only a note"
                )
                assert item["info"]["feedback"] == ("GOOD" if index % 2 == 0 else None)
                if enriched:
                    assert (
                        item["edition"]["isbn"]
                        == detail_fixture["isbns"][index][index % 2]
                    )
                    assert (
                        item["edition"]["illustrators"][0]["id"]
                        == detail_fixture["illustrator"]
                    )
                    assert (
                        item["edition"]["authors"][0]["id"] == detail_fixture["author"]
                    )
    finally:
        event.remove(session.bind, "after_cursor_execute", capture)


def test_missing_editions_keep_total_and_warn_after_serialization(
    session, detail_fixture, monkeypatch
):
    warnings = []

    def warn(**kwargs):
        warnings.append(kwargs)
        kwargs["session"].commit()

    monkeypatch.setattr("app.services.events.create_event", warn)
    with Session(session.bind) as cold:
        booklist = booklist_repository.get_or_404(cold, detail_fixture["id"])
        result = populate_booklist_object(
            booklist, cold, PaginatedQueryParams(skip=10, limit=2), True
        )
        assert [item.work_id for item in result.data] == [detail_fixture["works"][10]]
        assert result.pagination.total == 12
        assert len(warnings) == 1
        assert warnings[0]["info"]["work_id"] == detail_fixture["works"][11]
        assert result.data[0].edition.illustrators


def test_explicit_other_work_and_missing_isbn_fallback(session, detail_fixture):
    foreign_isbn = detail_fixture["isbns"][1][0]
    detail_fixture["items"][0].info = {
        "edition": f"{foreign_isbn[:3]}-{foreign_isbn[3:]}"
    }
    detail_fixture["items"][1].info = {"edition": generate_random_valid_isbn13()}
    detail_fixture["items"][2].info = {"edition": "invalid-isbn"}
    session.commit()
    with Session(session.bind) as cold:
        booklist = booklist_repository.get_or_404(cold, detail_fixture["id"])
        result = populate_booklist_object(
            booklist, cold, PaginatedQueryParams(skip=0, limit=3), True
        )
        assert result.data[0].work_id == detail_fixture["works"][0]
        assert result.data[0].edition.work_id == str(detail_fixture["works"][1])
        assert result.data[0].edition.isbn == detail_fixture["isbns"][1][0]
        assert str(result.data[0].work.authors[0].id) == detail_fixture["author"]
        assert (
            str(result.data[0].edition.authors[0].id)
            == detail_fixture["foreign_author"]
        )
        assert (
            result.data[0].edition.illustrators[0].id
            == detail_fixture["foreign_illustrator"]
        )
        assert result.data[1].edition.isbn == detail_fixture["isbns"][1][1]
        assert result.data[2].edition.isbn == detail_fixture["isbns"][2][1]


@pytest.mark.parametrize("null_field", ["cover_url", "date_published"])
def test_fallback_prefers_cover_and_known_publication_date(
    session, detail_fixture, null_field
):
    first = session.scalar(
        select(Edition).where(Edition.isbn == detail_fixture["isbns"][0][0])
    )
    second = session.scalar(
        select(Edition).where(Edition.isbn == detail_fixture["isbns"][0][1])
    )
    setattr(first, null_field, None)
    detail_fixture["items"][0].info = None
    session.commit()
    expected_isbn = second.isbn
    with Session(session.bind) as cold:
        booklist = booklist_repository.get_or_404(cold, detail_fixture["id"])
        result = populate_booklist_object(
            booklist, cold, PaginatedQueryParams(skip=0, limit=1), True
        )
        assert result.data[0].edition.isbn == expected_isbn


@pytest.mark.parametrize("missing_cover", [None, "", "   "])
def test_pinned_metadata_kept_with_alternative_cover(
    session, detail_fixture, missing_cover
):
    pinned = session.scalar(
        select(Edition).where(Edition.isbn == detail_fixture["isbns"][0][0])
    )
    pinned.cover_url = missing_cover
    session.commit()
    statements = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(session.bind, "after_cursor_execute", capture)
    try:
        with Session(session.bind) as cold:
            booklist = booklist_repository.get_or_404(cold, detail_fixture["id"])
            item = populate_booklist_object(
                booklist, cold, PaginatedQueryParams(skip=0, limit=1), True
            ).data[0]
            assert item.edition.isbn == detail_fixture["isbns"][0][0]
            assert item.edition.title == "Edition 0/0"
            assert item.edition.date_published == 2000
            assert item.edition.cover_url is None
            assert item.alternative_cover.source_isbn == detail_fixture["isbns"][0][1]
            assert str(item.alternative_cover.url) == "https://example.com/cover.jpg"
            assert item.observation_receipt
            assert len(statements) <= 10
    finally:
        event.remove(session.bind, "after_cursor_execute", capture)


@pytest.mark.parametrize(
    "covers",
    [
        ("https://example.com/z.jpg", "https://example.com/a.jpg"),
        (None, ""),
        (" ", None),
    ],
)
def test_fallback_orders_by_date_not_url(session, detail_fixture, covers):
    for isbn, cover in zip(detail_fixture["isbns"][0], covers):
        session.scalar(select(Edition).where(Edition.isbn == isbn)).cover_url = cover
    detail_fixture["items"][0].info = None
    session.commit()
    with Session(session.bind) as cold:
        booklist = booklist_repository.get_or_404(cold, detail_fixture["id"])
        item = populate_booklist_object(
            booklist, cold, PaginatedQueryParams(skip=0, limit=1), True
        ).data[0]
        assert item.edition.isbn == detail_fixture["isbns"][0][1]
        assert item.alternative_cover is None


def test_fallback_ties_are_stable(session, detail_fixture):
    for isbn in detail_fixture["isbns"][0]:
        session.scalar(
            select(Edition).where(Edition.isbn == isbn)
        ).date_published = None
    session.commit()
    with Session(session.bind) as cold:
        editions = booklist_repository.get_detail_editions(
            cold, [detail_fixture["works"][0]], {}
        )
        assert editions[detail_fixture["works"][0]].isbn == min(
            detail_fixture["isbns"][0]
        )


def test_foreign_pin_cover_comes_from_its_actual_work(session, detail_fixture):
    isbn = detail_fixture["isbns"][1][0]
    session.scalar(select(Edition).where(Edition.isbn == isbn)).cover_url = None
    detail_fixture["items"][0].info = {"edition": isbn}
    session.commit()
    with Session(session.bind) as cold:
        booklist = booklist_repository.get_or_404(cold, detail_fixture["id"])
        item = populate_booklist_object(
            booklist, cold, PaginatedQueryParams(skip=0, limit=1), True
        ).data[0]
        assert item.edition.isbn == isbn
        assert item.alternative_cover.source_isbn == detail_fixture["isbns"][1][1]


@pytest.mark.parametrize("enriched", [False, True])
def test_beyond_end_page_is_empty_without_hydration(session, detail_fixture, enriched):
    with Session(session.bind) as cold:
        booklist = booklist_repository.get_or_404(cold, detail_fixture["id"])
        response = populate_booklist_object(
            booklist, cold, PaginatedQueryParams(skip=100, limit=5), enriched
        )
        assert response.data == []
        assert response.pagination.total == 12

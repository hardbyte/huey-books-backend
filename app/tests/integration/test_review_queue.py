"""Review queue ordering and cover thumbnails.

Covers two behaviours of ``review_repository.get_review_queue``:
- it returns a cover image URL for each work (for the queue/modal thumbnails);
- it surfaces books that still need a human look (unchecked / AI-labelled)
  ahead of already human-reviewed books, so reviewing effort lands first on
  the books that need it.
"""

import uuid

from app.models.edition import Edition
from app.models.labelset import LabelOrigin, LabelSet
from app.models.work import Work, WorkType
from app.repositories.review_repository import review_repository


def _unique_isbn() -> str:
    # Unique within the shared test DB; format is unimportant for these tests.
    return f"978{uuid.uuid4().int % 10_000_000_000:010d}"


def _make_work(
    session,
    *,
    title: str,
    cover_url: str | None,
    hue_origin: LabelOrigin | None,
    checked: bool | None,
) -> Work:
    work = Work(type=WorkType.BOOK, title=title)
    session.add(work)
    session.flush()

    session.add(
        Edition(isbn=_unique_isbn(), work_id=work.id, cover_url=cover_url, info={})
    )
    session.add(LabelSet(work_id=work.id, hue_origin=hue_origin, checked=checked))
    session.commit()
    return work


def test_review_queue_includes_cover_url(session):
    cover = "https://covers.test/peach.jpg"
    work = _make_work(
        session,
        title=f"Cover Test {uuid.uuid4().hex[:8]}",
        cover_url=cover,
        hue_origin=LabelOrigin.CLUSTER_RELEVANCE,
        checked=False,
    )

    items, _ = review_repository.get_review_queue(
        db=session, status="all", limit=100_000
    )

    item = next((i for i in items if i["work_id"] == work.id), None)
    assert item is not None, "newly created work should appear in the queue"
    assert item["cover_url"] == cover


def test_review_queue_prioritises_books_needing_attention(session):
    """An AI-labelled, unchecked book should rank above a human-reviewed,
    checked one regardless of insertion order."""
    # Insert the already-done book first so insertion order can't explain a pass.
    done = _make_work(
        session,
        title=f"Done {uuid.uuid4().hex[:8]}",
        cover_url=None,
        hue_origin=LabelOrigin.HUMAN,
        checked=True,
    )
    needs = _make_work(
        session,
        title=f"Needs {uuid.uuid4().hex[:8]}",
        cover_url=None,
        hue_origin=LabelOrigin.CLUSTER_RELEVANCE,
        checked=False,
    )

    items, _ = review_repository.get_review_queue(
        db=session, status="all", limit=100_000
    )
    ids = [i["work_id"] for i in items]

    assert needs.id in ids and done.id in ids
    assert ids.index(needs.id) < ids.index(done.id)


def test_review_queue_human_reviewed_filter_excludes_unchecked(session):
    """Sanity check that the status filter still works after the ordering
    change: a freshly AI-labelled book is not in the human-reviewed view."""
    work = _make_work(
        session,
        title=f"Filter {uuid.uuid4().hex[:8]}",
        cover_url=None,
        hue_origin=LabelOrigin.CLUSTER_RELEVANCE,
        checked=False,
    )

    items, _ = review_repository.get_review_queue(
        db=session, status="human_reviewed", limit=100_000
    )
    assert work.id not in [i["work_id"] for i in items]

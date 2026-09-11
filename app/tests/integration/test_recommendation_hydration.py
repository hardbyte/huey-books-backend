from unittest.mock import AsyncMock

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import event, inspect, text
from sqlalchemy.orm.attributes import NO_VALUE

from app.api.recommendations import get_recommendations_with_fallback
from app.models import Collection, CollectionItem
from app.repositories.recommendation_repository import (
    RecommendationCandidate,
    recommendation_repository,
)
from app.schemas.labelset import LabelSetDetail
from app.schemas.recommendations import HueyBook, HueyRecommendationFilter
from app.services.recommendations import get_recommended_editions_from_mv
from app.tests.integration.test_recommendable_editions_mv import (
    _make_labeled_work,
    _refresh_mv,
)

HYDRATION_SELECT_BUDGET = 7


@pytest.mark.asyncio
async def test_recommendation_hydration_budget_and_title_fallback(
    session, async_session
):
    work, edition, labels = _make_labeled_work(
        session, "hydration-budget", hue_ids=[1], ra_ids=[1]
    )
    edition.title = None
    labels.checked = True
    session.commit()
    _refresh_mv(session)
    statements: list[str] = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    engine = async_session.bind.sync_engine
    event.listen(engine, "after_cursor_execute", capture)
    try:
        rows = await get_recommended_editions_from_mv(
            async_session, limit=10, boost_work_ids=[work.id]
        )
        matching = [(w, e, ls) for w, e, ls in rows if w.id == work.id]
        assert len(matching) == 1
        loaded_work, loaded_edition, loaded_labels = matching[0]
        result = HueyBook(
            work_id=loaded_work.id,
            isbn=loaded_edition.isbn,
            cover_url=loaded_edition.cover_url,
            display_title=loaded_edition.get_display_title(),
            authors_string=loaded_work.get_authors_string(),
            summary=loaded_labels.huey_summary,
            labels=LabelSetDetail.model_validate(loaded_labels),
        )
        assert result.display_title == work.title
        assert result.labels.checked is True
        assert result.labels.hues and result.labels.reading_abilities
        assert result.authors_string == work.get_authors_string()
        assert inspect(loaded_edition).attrs.collections.loaded_value is NO_VALUE
        assert inspect(loaded_edition).attrs.illustrators.loaded_value is NO_VALUE
        assert len(statements) <= HYDRATION_SELECT_BUDGET
        assert all(
            "count(collection_items.id)" not in statement for statement in statements
        )
    finally:
        event.remove(engine, "after_cursor_execute", capture)
        await async_session.rollback()
        for obj in (labels, edition, work):
            session.delete(obj)
        session.commit()


@pytest.mark.asyncio
async def test_candidate_hydration_preserves_order_missing_rows_and_actual_edition_work(
    session, async_session
):
    first = _make_labeled_work(session, "candidate-first", hue_ids=[1], ra_ids=[1])
    second = _make_labeled_work(session, "candidate-second", hue_ids=[1], ra_ids=[1])
    first_work, first_edition, first_labels = first
    second_work, second_edition, second_labels = second
    first_edition.work_id = second_work.id
    session.commit()
    session.execute(
        text("UPDATE editions SET title=NULL WHERE id=:id"), {"id": first_edition.id}
    )
    session.commit()
    first_candidate = RecommendationCandidate(
        first_work.id, first_labels.id, first_edition.isbn
    )
    second_candidate = RecommendationCandidate(
        second_work.id, second_labels.id, second_edition.isbn
    )
    try:
        rows = await recommendation_repository.load_ranked_candidates(
            async_session,
            [
                second_candidate,
                RecommendationCandidate(-1, -1, "missing"),
                first_candidate,
                second_candidate,
            ],
        )
        assert [w.id for w, _, _ in rows] == [
            second_work.id,
            first_work.id,
            second_work.id,
        ]
        assert rows[1][1].get_display_title() == second_work.title
        assert (
            await recommendation_repository.load_ranked_candidates(async_session, [])
            == []
        )
    finally:
        await async_session.rollback()
        for obj in (
            first_labels,
            second_labels,
            first_edition,
            second_edition,
            first_work,
            second_work,
        ):
            session.delete(obj)
        session.commit()


@pytest.mark.asyncio
async def test_author_diversity_still_applied_after_hydration(
    session, async_session, test_school, monkeypatch
):
    labelled = [
        _make_labeled_work(session, f"diversity-{index}", hue_ids=[1], ra_ids=[1])
        for index in range(3)
    ]
    labelled[1][0].authors = labelled[0][0].authors
    collection = Collection(
        name="Diversity isolated library", school_id=test_school.school_uuid
    )
    session.add(collection)
    session.flush()
    holdings = [
        CollectionItem(
            collection_id=collection.id,
            edition_isbn=edition.isbn,
            copies_total=1,
            copies_available=1,
        )
        for _, edition, _ in labelled
    ]
    session.add_all(holdings)
    session.commit()
    _refresh_mv(session)
    monkeypatch.setattr("app.api.recommendations.event_repository.acreate", AsyncMock())
    try:
        diverse, _ = await get_recommendations_with_fallback(
            async_session,
            None,
            test_school,
            HueyRecommendationFilter(age=8),
            BackgroundTasks(),
            limit=3,
            school_only=True,
        )
        assert len(diverse) == 2
        assert len({book.authors_string for book in diverse}) == 2
        all_books, _ = await get_recommendations_with_fallback(
            async_session,
            None,
            test_school,
            HueyRecommendationFilter(age=8),
            BackgroundTasks(),
            limit=3,
            school_only=True,
            remove_duplicate_authors=False,
        )
        assert len(all_books) == 3
        assert {book.work_id for book in all_books} == {
            work.id for work, _, _ in labelled
        }
    finally:
        await async_session.rollback()
        for obj in holdings:
            session.delete(obj)
        session.delete(collection)
        session.flush()
        for work, edition, labels in labelled:
            session.delete(labels)
            session.delete(edition)
            session.delete(work)
        session.commit()

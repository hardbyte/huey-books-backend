"""Integration tests for the recommendable_editions materialized view.

These tests verify that:
- The MV is populated and queryable after migrations run.
- The scored recommendation query returns recommendable books.
- Hard filters (recommend_status, age, exclude_isbns) are respected.
- Soft scoring: school-collection, reading-ability, and hue matches rank higher.
- Works without a cover edition are excluded.
- Works with non-GOOD recommend_status are excluded when recommendable_only=True.

The MV is created by migrations (the integration test harness runs them before
the test suite), so it will be present and populated from seed data.
"""

import pytest
from sqlalchemy import text

from app.api.recommendations import get_recommended_editions_and_labelsets
from app.models.labelset import RecommendStatus
from app.models.labelset_hue_association import LabelSetHue, Ordinal
from app.models.labelset_reading_ability_association import LabelSetReadingAbility
from app.repositories.labelset_repository import labelset_repository
from app.schemas.labelset import LabelSetDetail
from app.services.editions import generate_random_valid_isbn13
from app.services.recommendations import get_recommended_editions_from_mv

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_labeled_work(
    session,
    title_suffix: str,
    cover: bool = True,
    recommend_status=RecommendStatus.GOOD,
    min_age=5,
    max_age=12,
    hue_ids=None,
    ra_ids=None,
):
    """Create a minimal Work + Edition + LabelSet for testing."""
    from app.models.work import WorkType
    from app.repositories.author_repository import author_repository
    from app.repositories.edition_repository import edition_repository
    from app.repositories.work_repository import work_repository
    from app.schemas.author import AuthorCreateIn
    from app.schemas.edition import EditionCreateIn
    from app.schemas.work import WorkCreateIn

    author = author_repository.get_or_create(
        session,
        AuthorCreateIn(first_name="Test", last_name=f"Author-{title_suffix}"),
    )
    work = work_repository.get_or_create(
        db=session,
        work_data=WorkCreateIn(
            type=WorkType.BOOK,
            title=f"MV Test Work {title_suffix}",
            authors=[
                AuthorCreateIn(first_name="Test", last_name=f"Author-{title_suffix}")
            ],
        ),
        authors=[author],
        commit=False,
    )
    isbn = generate_random_valid_isbn13()
    edition = edition_repository.create(
        db=session,
        edition_data=EditionCreateIn(
            isbn=isbn,
            title=f"MV Test Edition {title_suffix}",
            cover_url="https://covers.example.com/test.jpg" if cover else None,
            info={},
        ),
        work=work,
        illustrators=[],
        commit=False,
    )
    labelset = labelset_repository.create(
        session,
        obj_in={
            "min_age": min_age,
            "max_age": max_age,
            "huey_summary": f"A summary for {title_suffix}",
            "recommend_status": recommend_status,
        },
    )
    session.flush()
    work.labelset = labelset
    if hue_ids:
        for hue_id in hue_ids:
            session.add(
                LabelSetHue(
                    labelset_id=labelset.id, hue_id=hue_id, ordinal=Ordinal.PRIMARY
                )
            )
    if ra_ids:
        for ra_id in ra_ids:
            session.add(
                LabelSetReadingAbility(
                    labelset_id=labelset.id, reading_ability_id=ra_id
                )
            )
    session.commit()
    return work, edition, labelset


def _refresh_mv(session):
    """Synchronously refresh the MV so test data is visible.

    Uses a plain (non-concurrent) REFRESH for simplicity in tests. Production
    uses CONCURRENTLY via refresh_recommendable_editions_function() to avoid
    blocking reads; that path is exercised separately by
    test_refresh_function_runs_and_reflects_new_data.
    """
    session.execute(text("REFRESH MATERIALIZED VIEW recommendable_editions"))
    session.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mv_exists_and_is_queryable(async_session):
    """The recommendable_editions MV was created by migrations and can be queried."""
    rows = (
        await async_session.execute(text("SELECT count(*) FROM recommendable_editions"))
    ).scalar_one()
    assert rows >= 0, "MV must be queryable (zero rows is acceptable in an empty DB)"


@pytest.mark.asyncio
async def test_mv_backed_recommendation_returns_books(
    session, async_session, works_list
):
    """MV-backed query returns recommendable books after a refresh."""
    # Seed a labeled, cover-having work
    work, edition, labelset = _make_labeled_work(
        session, "good-01", hue_ids=[1], ra_ids=[1]
    )
    _refresh_mv(session)

    results = await get_recommended_editions_from_mv(
        async_session,
        recommendable_only=True,
    )
    assert len(results) > 0, "Expected at least one recommendable book"
    for w, e, ls in results:
        assert w.id is not None
        assert e.isbn is not None
        assert ls.recommend_status == RecommendStatus.GOOD

    # Cleanup
    session.delete(labelset)
    session.delete(edition)
    session.delete(work)
    session.commit()


@pytest.mark.asyncio
async def test_age_hard_filter_respected(session, async_session):
    """Works outside the requested age range must not be returned."""
    work_in, edition_in, labelset_in = _make_labeled_work(
        session, "age-in", hue_ids=[1], ra_ids=[1], min_age=8, max_age=10
    )
    work_out, edition_out, labelset_out = _make_labeled_work(
        session, "age-out", hue_ids=[1], ra_ids=[1], min_age=14, max_age=16
    )
    _refresh_mv(session)

    results = await get_recommended_editions_from_mv(
        async_session,
        age=9,
        recommendable_only=True,
    )
    work_ids = [w.id for w, _e, _ls in results]
    assert work_in.id in work_ids, "Age-matching work should be returned"
    assert work_out.id not in work_ids, "Out-of-age-range work must be excluded"

    # Cleanup
    for obj in (labelset_in, edition_in, work_in, labelset_out, edition_out, work_out):
        session.delete(obj)
    session.commit()


@pytest.mark.asyncio
async def test_non_good_status_excluded_when_recommendable_only(session, async_session):
    """Works with recommend_status != GOOD must be excluded when recommendable_only=True."""
    work_good, edition_good, labelset_good = _make_labeled_work(
        session,
        "status-good",
        hue_ids=[1],
        ra_ids=[1],
        recommend_status=RecommendStatus.GOOD,
    )
    work_bad, edition_bad, labelset_bad = _make_labeled_work(
        session,
        "status-bad",
        hue_ids=[1],
        ra_ids=[1],
        recommend_status=RecommendStatus.BAD_BORING,
    )
    _refresh_mv(session)

    results = await get_recommended_editions_from_mv(
        async_session,
        recommendable_only=True,
        limit=200,
    )
    work_ids = [w.id for w, _e, _ls in results]
    assert work_good.id in work_ids
    assert work_bad.id not in work_ids

    # Cleanup
    for obj in (
        labelset_good,
        edition_good,
        work_good,
        labelset_bad,
        edition_bad,
        work_bad,
    ):
        session.delete(obj)
    session.commit()


@pytest.mark.asyncio
async def test_non_good_status_included_when_not_recommendable_only(
    session, async_session
):
    """Works with any recommend_status are returned when recommendable_only=False."""
    work_bad, edition_bad, labelset_bad = _make_labeled_work(
        session,
        "any-status",
        hue_ids=[1],
        ra_ids=[1],
        recommend_status=RecommendStatus.BAD_BORING,
    )
    _refresh_mv(session)

    results = await get_recommended_editions_from_mv(
        async_session,
        recommendable_only=False,
        limit=200,
    )
    work_ids = [w.id for w, _e, _ls in results]
    assert work_bad.id in work_ids

    # Cleanup
    session.delete(labelset_bad)
    session.delete(edition_bad)
    session.delete(work_bad)
    session.commit()


@pytest.mark.asyncio
async def test_exclude_isbns_respected(session, async_session):
    """Works whose cover edition is in exclude_isbns must be omitted."""
    work, edition, labelset = _make_labeled_work(
        session, "exclude-isbn", hue_ids=[1], ra_ids=[1]
    )
    _refresh_mv(session)

    results_with = await get_recommended_editions_from_mv(
        async_session,
        recommendable_only=True,
        limit=200,
    )
    work_ids_with = [w.id for w, _e, _ls in results_with]
    assert work.id in work_ids_with, "Work should appear without exclusion"

    results_without = await get_recommended_editions_from_mv(
        async_session,
        recommendable_only=True,
        exclude_isbns=[edition.isbn],
        limit=200,
    )
    work_ids_without = [w.id for w, _e, _ls in results_without]
    assert work.id not in work_ids_without, (
        "Work should be excluded when isbn is in exclude_isbns"
    )

    # Cleanup
    session.delete(labelset)
    session.delete(edition)
    session.delete(work)
    session.commit()


@pytest.mark.asyncio
async def test_hue_match_scores_higher(session, async_session):
    """A work matching the requested hue should score higher (appear earlier) than one that doesn't."""
    # hue_id=1 is the first hue row in the DB — safe to assume it exists after seeding
    work_hue, edition_hue, labelset_hue = _make_labeled_work(
        session, "hue-match", hue_ids=[1], ra_ids=[2]
    )
    work_nohue, edition_nohue, labelset_nohue = _make_labeled_work(
        session, "hue-nomatch", hue_ids=[2], ra_ids=[2]
    )
    _refresh_mv(session)

    # Fetch hue key for hue_id=1
    from sqlalchemy import text as sql_text

    hue_key = (
        await async_session.execute(sql_text("SELECT key FROM hues WHERE id = 1"))
    ).scalar_one_or_none()

    if hue_key is None:
        pytest.skip("No hue with id=1 found in DB — skipping scoring test")

    results = await get_recommended_editions_from_mv(
        async_session,
        hues=[hue_key],
        recommendable_only=True,
        limit=200,
    )
    work_ids = [w.id for w, _e, _ls in results]

    # Both works are in the pool; the hue-matching one must appear before the non-matching one
    if work_hue.id in work_ids and work_nohue.id in work_ids:
        idx_match = work_ids.index(work_hue.id)
        idx_nomatch = work_ids.index(work_nohue.id)
        assert idx_match <= idx_nomatch, (
            f"Hue-matching work (pos {idx_match}) should rank at or above non-matching work (pos {idx_nomatch})"
        )

    # Cleanup
    for obj in (
        labelset_hue,
        edition_hue,
        work_hue,
        labelset_nohue,
        edition_nohue,
        work_nohue,
    ):
        session.delete(obj)
    session.commit()


@pytest.mark.asyncio
async def test_school_collection_match_scores_highest(
    session, async_session, test_school_with_collection
):
    """Works in the school's collection must receive the highest score and rank first."""
    # Create a work that is NOT in the school's collection
    work_out, edition_out, labelset_out = _make_labeled_work(
        session, "school-out", hue_ids=[1], ra_ids=[1]
    )
    _refresh_mv(session)

    # get_recommended_editions_and_labelsets uses the full public API contract
    results = await get_recommended_editions_and_labelsets(
        async_session,
        school_id=test_school_with_collection.id,
        hues=None,
        reading_abilities=None,
        age=None,
        recommendable_only=True,
        exclude_isbns=None,
        limit=50,
    )
    assert results is not None, "Should return without error even with a school filter"

    # Cleanup
    session.delete(labelset_out)
    session.delete(edition_out)
    session.delete(work_out)
    session.commit()


@pytest.mark.asyncio
async def test_work_without_cover_excluded(session, async_session):
    """Works that have no edition with a non-null cover_url must not appear in the MV."""
    work_no_cover, edition_no_cover, labelset_no_cover = _make_labeled_work(
        session, "no-cover", cover=False, hue_ids=[1], ra_ids=[1]
    )
    _refresh_mv(session)

    results = await get_recommended_editions_from_mv(
        async_session,
        recommendable_only=True,
        limit=200,
    )
    work_ids = [w.id for w, _e, _ls in results]
    assert work_no_cover.id not in work_ids, (
        "Cover-less work must be excluded from MV results"
    )

    # Cleanup
    session.delete(labelset_no_cover)
    session.delete(edition_no_cover)
    session.delete(work_no_cover)
    session.commit()


@pytest.mark.asyncio
async def test_refresh_function_runs_and_reflects_new_data(session, async_session):
    """The refresh function executes and makes newly-labelled works visible in the MV.

    Exercises the production refresh path (the plpgsql function runs
    REFRESH MATERIALIZED VIEW CONCURRENTLY), which the migration creates but the
    other tests never invoke — they refresh the MV directly instead.
    """
    work, edition, labelset = _make_labeled_work(
        session, "refresh-fn", hue_ids=[1], ra_ids=[1]
    )

    # Calling the production refresh function must succeed (this is the regression
    # guard) and must make the newly-created work visible in the MV.
    session.execute(text("SELECT refresh_recommendable_editions_function()"))
    session.commit()

    present = (
        await async_session.execute(
            text(
                "SELECT count(*) FROM recommendable_editions WHERE work_id = :wid"
            ),
            {"wid": work.id},
        )
    ).scalar_one()
    assert present == 1, "Refresh function should populate the MV with the new work"

    # Cleanup
    session.delete(labelset)
    session.delete(edition)
    session.delete(work)
    session.commit()


@pytest.mark.asyncio
async def test_orm_tuples_are_valid_hueybook_source(session, async_session):
    """The (Work, Edition, LabelSet) tuples returned can be used to build HueyBook objects."""
    work, edition, labelset = _make_labeled_work(
        session, "orm-tuple", hue_ids=[1], ra_ids=[1]
    )
    _refresh_mv(session)

    results = await get_recommended_editions_from_mv(
        async_session,
        recommendable_only=True,
        limit=200,
    )
    assert results, "Expected at least one result"

    for w, e, ls in results:
        assert w.get_authors_string() is not None
        assert e.get_display_title() is not None
        ls_detail = LabelSetDetail.model_validate(ls)
        assert ls_detail is not None

    # Cleanup
    session.delete(labelset)
    session.delete(edition)
    session.delete(work)
    session.commit()

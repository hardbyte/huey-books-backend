import pytest
from sqlalchemy import delete, select

from app.models.labelset import LabelSet
from app.models.review import Review, ReviewableType
from app.models.work import Work, WorkType
from app.repositories.labelset_repository import labelset_repository


@pytest.fixture
def review_work(session):
    work = Work(title="Atomic review fixture", type=WorkType.BOOK)
    session.add(work)
    session.commit()
    identifier = work.id
    yield work
    session.rollback()
    for labelset_id in session.scalars(
        select(LabelSet.id).where(LabelSet.work_id == identifier)
    ):
        session.execute(
            delete(Review).where(
                Review.reviewable_type == ReviewableType.LABELSET,
                Review.reviewable_id == str(labelset_id),
            )
        )
    session.execute(delete(LabelSet).where(LabelSet.work_id == identifier))
    session.execute(delete(Work).where(Work.id == identifier))
    session.commit()


@pytest.mark.parametrize("existing_labels", [False, True])
def test_failed_review_promotion_rolls_back_review_and_canonical_labels(
    client,
    session,
    review_work,
    test_schooladmin_account_headers,
    monkeypatch,
    existing_labels,
):
    labelset = (
        labelset_repository.get_or_create(session, review_work, commit=True)
        if existing_labels
        else None
    )
    labelset_id = labelset.id if labelset else None
    original_age = labelset.min_age if labelset else None
    original_origin = labelset.age_origin if labelset else None
    original_review_ids = set(session.scalars(select(Review.id)))
    promote = labelset_repository.patch

    def fail_after_promotion(*args, **kwargs):
        promote(*args, **kwargs)
        args[0].flush()
        raise RuntimeError("Synthetic failure after promotion")

    monkeypatch.setattr(labelset_repository, "patch", fail_after_promotion)
    with pytest.raises(RuntimeError, match="Synthetic failure after promotion"):
        client.post(
            f"/v1/work/{review_work.id}/reviews",
            headers=test_schooladmin_account_headers,
            json={"min_age": 1, "notes": "Atomic review test"},
        )
    session.rollback()
    if existing_labels:
        session.refresh(labelset)
        assert (labelset.min_age, labelset.age_origin) == (
            original_age,
            original_origin,
        )
    assert set(
        session.scalars(select(LabelSet.id).where(LabelSet.work_id == review_work.id))
    ) == ({labelset_id} if existing_labels else set())
    assert set(session.scalars(select(Review.id))) == original_review_ids

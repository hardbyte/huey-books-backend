import pytest
from sqlalchemy import delete, select

from app.api import reviews
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
    session.execute(delete(LabelSet).where(LabelSet.work_id == identifier))
    session.execute(delete(Work).where(Work.id == identifier))
    session.commit()


def test_failed_review_promotion_rolls_back_review_and_canonical_labels(
    client, session, review_work, test_schooladmin_account_headers, monkeypatch
):
    labelset = labelset_repository.get_or_create(session, review_work, commit=True)
    labelset_id = labelset.id
    original_age = labelset.min_age
    original_origin = labelset.age_origin
    original_review_ids = set(
        session.scalars(
            select(Review.id).where(
                Review.reviewable_type == ReviewableType.LABELSET,
                Review.reviewable_id == str(labelset_id),
            )
        )
    )
    promote = reviews._promote_review_to_canonical

    def fail_after_promotion(*args, **kwargs):
        promote(*args, **kwargs)
        args[0].flush()
        raise RuntimeError("Synthetic failure after promotion")

    monkeypatch.setattr(reviews, "_promote_review_to_canonical", fail_after_promotion)
    with pytest.raises(RuntimeError, match="Synthetic failure after promotion"):
        client.post(
            f"/v1/work/{review_work.id}/reviews",
            headers=test_schooladmin_account_headers,
            json={"min_age": 1, "notes": "Atomic review test"},
        )
    session.rollback()
    session.refresh(labelset)
    assert (labelset.min_age, labelset.age_origin) == (original_age, original_origin)
    assert (
        set(
            session.scalars(
                select(Review.id).where(
                    Review.reviewable_type == ReviewableType.LABELSET,
                    Review.reviewable_id == str(labelset_id),
                )
            )
        )
        == original_review_ids
    )

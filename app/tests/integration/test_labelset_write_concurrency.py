from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import User
from app.models.labelset import LabelOrigin, LabelSet
from app.models.review import Review, ReviewableType
from app.models.work import Work, WorkType
from app.repositories.labelset_repository import labelset_repository
from app.schemas.labelset import LabelSetCreateIn
from app.schemas.review import LabelSetReviewIn
from app.services.reviews import ReviewService


@pytest.fixture
def work_id(session):
    work = Work(title="Concurrent review fixture", type=WorkType.BOOK)
    session.add(work)
    session.flush()
    identifier = work.id
    session.commit()
    yield identifier
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


def test_stale_writer_cannot_overwrite_new_human_authority(session, work_id):
    labels = labelset_repository.get_or_create(session, session.get(Work, work_id))
    labelset_repository.patch(
        session, labels, LabelSetCreateIn(min_age=3, age_origin=LabelOrigin.OTHER)
    )
    with Session(session.get_bind()) as stale_session:
        stale = stale_session.get(LabelSet, labels.id)
        assert stale.age_origin == LabelOrigin.OTHER
        labelset_repository.patch(
            session, labels, LabelSetCreateIn(min_age=12, age_origin=LabelOrigin.HUMAN)
        )
        labelset_repository.patch(
            stale_session,
            stale,
            LabelSetCreateIn(min_age=5, age_origin=LabelOrigin.EDUCATOR),
        )
        assert stale.min_age == 12
        assert stale.age_origin == LabelOrigin.HUMAN


def test_concurrent_first_reviews_share_one_labelset(
    session, work_id, test_schooladmin_account
):
    account_id = test_schooladmin_account.id
    engine = session.get_bind()
    session.rollback()
    both_loaded = Barrier(2)

    def submit_first_review():
        with Session(engine) as writer:
            work = writer.get(Work, work_id)
            account = writer.get(User, account_id)
            assert work.labelset is None
            both_loaded.wait(timeout=10)
            review = ReviewService().submit(
                writer, work, account, LabelSetReviewIn(min_age=3, max_age=5)
            )
            return review.reviewable_id

    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(submit_first_review) for _ in range(2)]
        labelset_ids = [future.result(timeout=20) for future in futures]
    assert labelset_ids[0] == labelset_ids[1]
    assert (
        len(
            session.scalars(
                select(LabelSet.id).where(LabelSet.work_id == work_id)
            ).all()
        )
        == 1
    )

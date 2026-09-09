from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models import User, Work
from app.models.labelset import LabelOrigin, LabelSet
from app.models.review import Review
from app.models.user import UserAccountType
from app.repositories.labelset_repository import LabelsetRepository, labelset_repository
from app.repositories.review_repository import ReviewRepositoryImpl, review_repository
from app.schemas.labelset import LabelSetCreateIn
from app.schemas.review import LabelSetReviewIn
from app.services.exceptions import ServiceException


class InvalidReviewError(ServiceException):
    pass


class ReviewConflictError(ServiceException):
    pass


class ReviewNotAllowedError(ServiceException):
    pass


class ReviewService:
    def __init__(
        self,
        labelsets: LabelsetRepository = labelset_repository,
        reviews: ReviewRepositoryImpl = review_repository,
    ):
        self.labelsets = labelsets
        self.reviews = reviews

    def submit(
        self, session: Session, work: Work, account: User, review_data: LabelSetReviewIn
    ) -> Review:
        if account.type not in (
            UserAccountType.WRIVETED,
            UserAccountType.EDUCATOR,
            UserAccountType.SCHOOL_ADMIN,
        ):
            raise ReviewNotAllowedError("Only staff and educators can submit reviews")
        try:
            labelset, current = self.labelsets.get_for_review(session, work)
            _validate_confirmation(labelset, current, review_data)
            review = self.reviews.upsert_review(
                db=session,
                labelset_id=labelset.id,
                reviewer_user_id=account.id,
                data=review_data,
                commit=False,
            )
            staff_review = account.type == UserAccountType.WRIVETED
            _promote_review_to_canonical(
                self.labelsets,
                session,
                labelset,
                review_data,
                account,
                origin=LabelOrigin.HUMAN if staff_review else LabelOrigin.EDUCATOR,
                mark_checked=staff_review
                and all(
                    value is not None
                    for value in (
                        review_data.hue_primary_key,
                        review_data.reading_ability_key
                        or review_data.expected_reading_ability_keys,
                        review_data.min_age,
                        review_data.max_age,
                        review_data.recommend_status,
                    )
                ),
            )
            session.commit()
            return review
        except Exception:
            session.rollback()
            raise


def _validate_confirmation(
    labelset: LabelSet, current: dict[str, Any], review_data: LabelSetReviewIn
) -> None:
    minimum = (
        review_data.min_age if review_data.min_age is not None else labelset.min_age
    )
    maximum = (
        review_data.max_age if review_data.max_age is not None else labelset.max_age
    )
    if minimum is not None and maximum is not None and minimum > maximum:
        raise InvalidReviewError("Minimum age must not exceed maximum age")
    if review_data.confirmed_existing:
        expected = {
            "hue_primary_key": current.get("primary_hue_key"),
            "hue_secondary_key": current.get("secondary_hue_key"),
            "hue_tertiary_key": current.get("tertiary_hue_key"),
            "min_age": labelset.min_age,
            "max_age": labelset.max_age,
            "reading_ability_key": current["reading_ability_keys"][0]
            if len(current["reading_ability_keys"]) == 1
            else None,
            "recommend_status": labelset.recommend_status,
        }
        required = (
            "hue_primary_key",
            "min_age",
            "max_age",
            "recommend_status",
        )
        reading_snapshot = review_data.expected_reading_ability_keys
        reading_matches = (
            set(reading_snapshot) == set(current["reading_ability_keys"])
            if reading_snapshot is not None
            else expected["reading_ability_key"] is not None
            and review_data.reading_ability_key == expected["reading_ability_key"]
        )
        if (
            not reading_matches
            or any(expected[key] is None for key in required)
            or any(
                getattr(review_data, key) != value
                for key, value in expected.items()
                if key != "reading_ability_key"
            )
        ):
            raise ReviewConflictError(
                "Labels are incomplete or have changed. Reload and submit your proposed labels instead of confirming.",
            )


def _promote_review_to_canonical(
    labelsets: LabelsetRepository,
    session: Session,
    labelset: LabelSet,
    review_data: LabelSetReviewIn,
    account: User,
    *,
    origin: LabelOrigin,
    mark_checked: bool,
) -> None:
    """Apply a reviewer's assessment to the canonical labelset.

    `origin` sets the authority weight used by the labelset repository, which
    only overwrites a field when its existing origin has equal-or-lower weight
    (so EDUCATOR beats AI but never overrides Wriveted HUMAN labels).
    `mark_checked` records Wriveted staff confirmation; teacher promotions
    preserve the existing checked flag rather than clearing it.
    """
    patch_data = LabelSetCreateIn(
        hue_primary_key=review_data.hue_primary_key,
        hue_secondary_key=review_data.hue_secondary_key,
        hue_tertiary_key=review_data.hue_tertiary_key,
        hue_origin=origin if review_data.hue_primary_key else None,
        min_age=review_data.min_age,
        max_age=review_data.max_age,
        age_origin=origin
        if review_data.min_age is not None or review_data.max_age is not None
        else None,
        reading_ability_keys=[review_data.reading_ability_key]
        if review_data.reading_ability_key
        else None,
        reading_ability_origin=origin if review_data.reading_ability_key else None,
        recommend_status=review_data.recommend_status,
        recommend_status_origin=origin if review_data.recommend_status else None,
        checked=True if mark_checked else labelset.checked,
        labelled_by_user_id=account.id,
    )

    labelsets.patch(session, labelset, patch_data, commit=False)

    if mark_checked:
        labelset.checked = True
        labelset.checked_at = datetime.utcnow()

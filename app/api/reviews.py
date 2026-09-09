from datetime import datetime
from typing import Optional, Union
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Path, Query
from fastapi_permissions import All, Allow
from sqlalchemy import select
from sqlalchemy.orm import Session
from structlog import get_logger

from app.api.common.pagination import PaginatedQueryParams
from app.api.dependencies.security import (
    get_current_active_superuser,
    get_current_active_user_or_service_account,
)
from app.db.session import get_session
from app.models import School, ServiceAccount, User, Work
from app.models.labelset import LabelOrigin, LabelSet
from app.models.user import UserAccountType
from app.permissions import Permission
from app.repositories.labelset_repository import labelset_repository
from app.repositories.review_repository import review_repository
from app.repositories.work_repository import work_repository
from app.schemas.labelset import LabelSetCreateIn
from app.schemas.pagination import PaginatedResponse, Pagination
from app.schemas.review import (
    LabelSetReviewDetail,
    LabelSetReviewIn,
    ReviewQueueItem,
    ReviewStats,
)
from app.services.recommendations import enqueue_debounced_mv_refresh

logger = get_logger()

review_acl = [
    (Allow, "role:admin", All),
    (Allow, "role:educator", All),
    (Allow, "role:schooladmin", All),
]

router = APIRouter(
    tags=["Reviews"],
    dependencies=[Depends(get_current_active_user_or_service_account)],
)


def get_work(
    work_id: int = Path(..., description="Work ID"),
    session: Session = Depends(get_session),
) -> Work:
    return work_repository.get_or_404(db=session, id=work_id)


def _review_to_detail(review) -> LabelSetReviewDetail:
    """Convert a Review ORM object to a LabelSetReviewDetail schema."""
    assessment = review.assessment or {}
    return LabelSetReviewDetail(
        id=review.id,
        labelset_id=int(review.reviewable_id),
        reviewer_user_id=review.reviewer_user_id,
        reviewer_name=review.reviewer.name if review.reviewer else None,
        hue_primary_key=assessment.get("hue_primary_key"),
        hue_secondary_key=assessment.get("hue_secondary_key"),
        hue_tertiary_key=assessment.get("hue_tertiary_key"),
        min_age=assessment.get("min_age"),
        max_age=assessment.get("max_age"),
        reading_ability_key=assessment.get("reading_ability_key"),
        expected_reading_ability_keys=assessment.get("expected_reading_ability_keys"),
        recommend_status=assessment.get("recommend_status"),
        notes=review.notes,
        confirmed_existing=assessment.get("confirmed_existing"),
        ai_assistance=assessment.get("ai_assistance"),
        created_at=review.created_at,
        updated_at=review.updated_at,
    )


@router.post(
    "/work/{work_id}/reviews",
    response_model=LabelSetReviewDetail,
    dependencies=[Permission("create", review_acl)],
)
def submit_review(
    review_data: LabelSetReviewIn,
    background_tasks: BackgroundTasks,
    work: Work = Depends(get_work),
    account: Union[User, ServiceAccount] = Depends(
        get_current_active_user_or_service_account
    ),
    session: Session = Depends(get_session),
):
    """
    Submit or update a review for a work's labelset.

    Each reviewer gets one review per labelset (upserted on conflict).
    Reviews also update the canonical labelset: Wriveted staff with HUMAN
    origin (and mark it checked), teachers with EDUCATOR origin.
    """
    if not isinstance(account, User):
        logger.warning("Service accounts cannot submit reviews")
        raise HTTPException(status_code=403, detail="Only users can submit reviews")

    labelset = labelset_repository.get_or_create(session, work, commit=True)
    session.execute(
        select(LabelSet.id).where(LabelSet.id == labelset.id).with_for_update()
    ).scalar_one()
    session.refresh(labelset)

    current = labelset.get_label_dict(session)
    minimum = (
        review_data.min_age if review_data.min_age is not None else labelset.min_age
    )
    maximum = (
        review_data.max_age if review_data.max_age is not None else labelset.max_age
    )
    if minimum is not None and maximum is not None and minimum > maximum:
        raise HTTPException(422, "Minimum age must not exceed maximum age")
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
            raise HTTPException(
                409,
                "Labels are incomplete or have changed. Reload and submit your proposed labels instead of confirming.",
            )

    review = review_repository.upsert_review(
        db=session,
        labelset_id=labelset.id,
        reviewer_user_id=account.id,
        data=review_data,
        commit=False,
    )

    # Promote the review into the canonical labelset so it influences
    # recommendations. Wriveted staff carry HUMAN authority and mark the
    # labelset as staff-checked; teachers carry EDUCATOR authority (above AI,
    # below staff) and leave the staff-confirmation flag untouched, so their
    # input improves recommendations immediately without bypassing QA.
    promoted = False
    if account.type == UserAccountType.WRIVETED:
        _promote_review_to_canonical(
            session,
            labelset,
            review_data,
            account,
            origin=LabelOrigin.HUMAN,
            mark_checked=all(
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
        promoted = True
    elif account.type in (UserAccountType.EDUCATOR, UserAccountType.SCHOOL_ADMIN):
        _promote_review_to_canonical(
            session,
            labelset,
            review_data,
            account,
            origin=LabelOrigin.EDUCATOR,
            mark_checked=False,
        )
        promoted = True

    session.commit()

    if promoted:
        # A promoted review mutates the canonical labelset, changing
        # recommendation scoring/eligibility, so debounce a MV refresh
        # (see enqueue_debounced_mv_refresh). Non-promoting reviews (e.g. from
        # students) don't touch the labelset, so no refresh is needed.
        background_tasks.add_task(enqueue_debounced_mv_refresh)

    logger.info(
        "Review submitted",
        work_id=work.id,
        reviewer=account.id,
        reviewer_type=account.type.value,
    )

    return _review_to_detail(review)


@router.get(
    "/work/{work_id}/reviews",
    response_model=list[LabelSetReviewDetail],
    dependencies=[Permission("read", review_acl)],
)
def get_reviews(
    work: Work = Depends(get_work),
    session: Session = Depends(get_session),
):
    """List all reviews for a work."""
    reviews = review_repository.get_reviews_for_work(db=session, work_id=work.id)
    return [_review_to_detail(r) for r in reviews]


@router.get(
    "/review-queue",
    response_model=PaginatedResponse[ReviewQueueItem],
    dependencies=[Permission("read", review_acl)],
)
def get_review_queue(
    status: str = Query(
        "all",
        description="Filter by review status",
        pattern="^(unchecked|ai_labelled|human_reviewed|all)$",
    ),
    min_school_count: int = Query(0, ge=0, description="Minimum school count"),
    school_id: Optional[UUID] = Query(
        None,
        description=(
            "Restrict the queue to a school's collection, identified by the "
            "school's wriveted_identifier. Honoured for Wriveted staff and "
            "service accounts; ignored for teachers, who are always scoped to "
            "their own school."
        ),
    ),
    pagination: PaginatedQueryParams = Depends(),
    account: Union[User, ServiceAccount] = Depends(
        get_current_active_user_or_service_account
    ),
    session: Session = Depends(get_session),
):
    """
    Prioritized review queue sorted by popularity (school_count DESC).

    Teachers (educators / school admins) see only books in their own school's
    collection — the ones their students actually read. Wriveted staff and
    service accounts see the global queue, or a chosen school's books when
    ``school_id`` (a school's wriveted_identifier) is supplied.
    """
    if isinstance(account, User) and account.type in (
        UserAccountType.EDUCATOR,
        UserAccountType.SCHOOL_ADMIN,
    ):
        # Teachers are always locked to their own school's books.
        effective_school_id = getattr(account, "school_id", None)
    elif school_id is not None:
        # Wriveted staff / service accounts may target any school by its public
        # identifier; resolve it to the internal id the repository filters on.
        effective_school_id = session.scalar(
            select(School.id).where(School.wriveted_identifier == school_id)
        )
    else:
        effective_school_id = None

    items, total = review_repository.get_review_queue(
        db=session,
        status=status,
        min_school_count=min_school_count,
        skip=pagination.skip,
        limit=pagination.limit,
        school_id=effective_school_id,
    )
    return PaginatedResponse(
        data=[ReviewQueueItem(**item) for item in items],
        pagination=Pagination(
            skip=pagination.skip,
            limit=pagination.limit,
            total=total,
        ),
    )


@router.get(
    "/review-stats",
    response_model=ReviewStats,
    dependencies=[Depends(get_current_active_superuser)],
)
def get_review_stats(
    session: Session = Depends(get_session),
):
    """
    Review dashboard statistics. Admin-only.
    """
    stats = review_repository.get_review_stats(db=session)
    return ReviewStats(**stats)


def _promote_review_to_canonical(
    session: Session,
    labelset,
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

    labelset_repository.patch(session, labelset, patch_data, commit=False)

    if mark_checked:
        labelset.checked = True
        labelset.checked_at = datetime.utcnow()

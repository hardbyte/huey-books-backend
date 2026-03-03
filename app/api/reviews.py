from datetime import datetime
from typing import Union

from fastapi import APIRouter, Depends, Path, Query
from fastapi_permissions import All, Allow
from sqlalchemy.orm import Session
from structlog import get_logger

from app.api.common.pagination import PaginatedQueryParams
from app.api.dependencies.security import (
    get_current_active_superuser,
    get_current_active_user_or_service_account,
)
from app.db.session import get_session
from app.models import ServiceAccount, User, Work
from app.models.labelset import LabelOrigin
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
    """Convert a LabelSetReview ORM object to a LabelSetReviewDetail schema."""
    return LabelSetReviewDetail(
        id=review.id,
        labelset_id=review.labelset_id,
        reviewer_user_id=review.reviewer_user_id,
        reviewer_name=review.reviewer.name if review.reviewer else None,
        hue_primary_key=review.hue_primary_key,
        hue_secondary_key=review.hue_secondary_key,
        hue_tertiary_key=review.hue_tertiary_key,
        min_age=review.min_age,
        max_age=review.max_age,
        reading_ability_key=review.reading_ability_key,
        recommend_status=review.recommend_status,
        notes=review.notes,
        confirmed_existing=review.confirmed_existing,
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
    work: Work = Depends(get_work),
    account: Union[User, ServiceAccount] = Depends(
        get_current_active_user_or_service_account
    ),
    session: Session = Depends(get_session),
):
    """
    Submit or update a review for a work's labelset.

    Each reviewer gets one review per labelset (upserted on conflict).
    Admin reviews also update the canonical labelset with HUMAN origin.
    """
    if not isinstance(account, User):
        logger.warning("Service accounts cannot submit reviews")
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="Only users can submit reviews")

    labelset = labelset_repository.get_or_create(session, work, commit=True)

    review = review_repository.upsert_review(
        db=session,
        labelset_id=labelset.id,
        reviewer_user_id=account.id,
        data=review_data,
        commit=True,
    )

    # Admin reviews auto-promote to canonical labelset
    if account.type == UserAccountType.WRIVETED:
        _promote_review_to_canonical(session, labelset, review_data, account)

    logger.info(
        "Review submitted",
        work_id=work.id,
        reviewer=account.id,
        is_admin=account.type == UserAccountType.WRIVETED,
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
    pagination: PaginatedQueryParams = Depends(),
    session: Session = Depends(get_session),
):
    """
    Prioritized review queue sorted by popularity (school_count DESC).

    Accessible to admins, educators, and school admins.
    """
    items, total = review_repository.get_review_queue(
        db=session,
        status=status,
        min_school_count=min_school_count,
        skip=pagination.skip,
        limit=pagination.limit,
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
) -> None:
    """Apply an admin's review assessment to the canonical labelset."""
    patch_data = LabelSetCreateIn(
        hue_primary_key=review_data.hue_primary_key,
        hue_secondary_key=review_data.hue_secondary_key,
        hue_tertiary_key=review_data.hue_tertiary_key,
        hue_origin=LabelOrigin.HUMAN if review_data.hue_primary_key else None,
        min_age=review_data.min_age,
        max_age=review_data.max_age,
        age_origin=LabelOrigin.HUMAN
        if review_data.min_age is not None or review_data.max_age is not None
        else None,
        recommend_status=review_data.recommend_status,
        recommend_status_origin=LabelOrigin.HUMAN
        if review_data.recommend_status
        else None,
        checked=True,
        labelled_by_user_id=account.id,
    )

    labelset_repository.patch(session, labelset, patch_data, commit=True)

    labelset.checked = True
    labelset.checked_at = datetime.utcnow()
    session.commit()

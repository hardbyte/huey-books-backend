from typing import Optional, Union
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Path, Query
from fastapi_permissions import All, Allow
from sqlalchemy import select
from sqlalchemy.orm import Session
from structlog import get_logger

from app.api.common.pagination import PaginatedQueryParams
from app.api.dependencies.async_db_dep import DBSessionDep
from app.api.dependencies.security import (
    get_current_active_superuser,
    get_current_active_user_or_service_account,
)
from app.db.session import get_session
from app.models import School, ServiceAccount, User, Work
from app.models.user import UserAccountType
from app.permissions import Permission
from app.repositories.review_repository import review_repository
from app.repositories.work_repository import work_repository
from app.schemas.pagination import PaginatedResponse, Pagination
from app.schemas.review import (
    LabelSetReviewDetail,
    LabelSetReviewIn,
    ReviewQueueItem,
    ReviewStats,
)
from app.services.recommendations import enqueue_debounced_mv_refresh
from app.services.reviews import (
    InvalidReviewError,
    ReviewConflictError,
    ReviewNotAllowedError,
    ReviewService,
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

    try:
        review = ReviewService().submit(session, work, account, review_data)
    except ReviewNotAllowedError as error:
        raise HTTPException(403, str(error)) from error
    except ReviewConflictError as error:
        raise HTTPException(409, str(error)) from error
    except InvalidReviewError as error:
        raise HTTPException(422, str(error)) from error

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
async def get_review_queue(
    session: DBSessionDep,
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
        if effective_school_id is None:
            raise HTTPException(status_code=403, detail="School membership required")
    elif school_id is not None:
        # Wriveted staff / service accounts may target any school by its public
        # identifier; resolve it to the internal id the repository filters on.
        effective_school_id = await session.scalar(
            select(School.id).where(School.wriveted_identifier == school_id)
        )
        if effective_school_id is None:
            raise HTTPException(status_code=404, detail="School not found")
    else:
        effective_school_id = None

    items, total = await review_repository.get_review_queue(
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

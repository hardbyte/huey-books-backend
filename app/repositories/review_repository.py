"""
Review repository - domain-focused data access for reviews.
"""

import uuid
from typing import Optional

from sqlalchemy import String, case, cast, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from structlog import get_logger

from app.models.author import Author
from app.models.author_work_association import author_work_association_table
from app.models.hue import Hue
from app.models.labelset import LabelOrigin, LabelSet
from app.models.labelset_hue_association import LabelSetHue, Ordinal
from app.models.review import Review, ReviewableType
from app.models.user import User
from app.models.work import Work
from app.models.work_collection_frequency import work_collection_frequency
from app.schemas.review import LabelSetReviewIn

logger = get_logger()

AI_ORIGINS = [
    LabelOrigin.GPT4,
    LabelOrigin.VERTEXAI,
    LabelOrigin.PREDICTED_NIELSEN,
    LabelOrigin.NIELSEN_CBMC,
    LabelOrigin.NIELSEN_BIC,
    LabelOrigin.NIELSEN_THEMA,
    LabelOrigin.NIELSEN_IA,
    LabelOrigin.NIELSEN_RA,
    LabelOrigin.CLUSTER_RELEVANCE,
    LabelOrigin.CLUSTER_ZAINAB,
]


class ReviewRepositoryImpl:
    """Repository for review operations."""

    def upsert_review(
        self,
        db: Session,
        labelset_id: int,
        reviewer_user_id: uuid.UUID,
        data: LabelSetReviewIn,
        commit: bool = True,
    ) -> Review:
        """Create or update a review for a labelset by a specific reviewer."""
        assessment = {}
        assessment_fields = [
            "hue_primary_key",
            "hue_secondary_key",
            "hue_tertiary_key",
            "min_age",
            "max_age",
            "reading_ability_key",
            "confirmed_existing",
        ]
        input_data = data.model_dump(exclude_none=True)
        for field in assessment_fields:
            assessment[field] = input_data.get(field)

        # Store recommend_status as its string value for JSON serialization
        if data.recommend_status is not None:
            assessment["recommend_status"] = data.recommend_status.value
        else:
            assessment["recommend_status"] = None

        values = {
            "reviewable_type": ReviewableType.LABELSET,
            "reviewable_id": str(labelset_id),
            "reviewer_user_id": reviewer_user_id,
            "notes": data.notes,
            "assessment": assessment,
        }

        update_values = {
            "notes": data.notes,
            "assessment": assessment,
            "updated_at": func.current_timestamp(),
        }

        stmt = (
            pg_insert(Review)
            .values(**values)
            .on_conflict_do_update(
                constraint="uq_reviews_type_entity_reviewer",
                set_=update_values,
            )
            .returning(Review.id)
        )

        result = db.execute(stmt)
        review_id = result.scalar_one()

        if commit:
            db.commit()

        review = db.get(Review, review_id)
        if commit:
            db.refresh(review)
        return review

    def get_reviews_for_labelset(
        self,
        db: Session,
        labelset_id: int,
    ) -> list[Review]:
        """Get all reviews for a specific labelset."""
        stmt = (
            select(Review)
            .where(
                Review.reviewable_type == ReviewableType.LABELSET,
                Review.reviewable_id == str(labelset_id),
            )
            .order_by(Review.updated_at.desc())
        )
        return db.execute(stmt).scalars().all()

    def get_reviews_for_work(
        self,
        db: Session,
        work_id: int,
    ) -> list[Review]:
        """Get all reviews for a work (via its labelset)."""
        stmt = (
            select(Review)
            .join(
                LabelSet,
                (Review.reviewable_id == cast(LabelSet.id, String))
                & (Review.reviewable_type == ReviewableType.LABELSET),
            )
            .where(LabelSet.work_id == work_id)
            .order_by(Review.updated_at.desc())
        )
        return db.execute(stmt).scalars().all()

    def get_review(
        self,
        db: Session,
        labelset_id: int,
        reviewer_user_id: uuid.UUID,
    ) -> Optional[Review]:
        """Get a specific review by labelset and reviewer."""
        stmt = select(Review).where(
            Review.reviewable_type == ReviewableType.LABELSET,
            Review.reviewable_id == str(labelset_id),
            Review.reviewer_user_id == reviewer_user_id,
        )
        return db.execute(stmt).scalar_one_or_none()

    def get_review_queue(
        self,
        db: Session,
        status: str = "all",
        min_school_count: int = 0,
        skip: int = 0,
        limit: int = 100,
    ) -> tuple[list[dict], int]:
        """
        Get a prioritized review queue of works, sorted by popularity.

        Returns (items, total_count).
        """
        awa = author_work_association_table
        cf = work_collection_frequency

        # Subquery for review aggregation per entity
        review_agg = (
            select(
                Review.reviewable_id.label("entity_id"),
                func.count(Review.id).label("review_count"),
                func.array_agg(User.name).label("reviewer_names"),
            )
            .join(User, Review.reviewer_user_id == User.id)
            .where(Review.reviewable_type == ReviewableType.LABELSET)
            .group_by(Review.reviewable_id)
            .subquery("review_agg")
        )

        # Subquery for author names per work
        author_agg = (
            select(
                awa.c.work_id,
                func.array_agg(
                    func.coalesce(Author.first_name, "")
                    + cast(" ", String)
                    + func.coalesce(Author.last_name, "")
                ).label("author_names"),
            )
            .join(Author, awa.c.author_id == Author.id)
            .group_by(awa.c.work_id)
            .subquery("author_agg")
        )

        # Main query
        stmt = (
            select(
                Work.id.label("work_id"),
                Work.title,
                Work.subtitle,
                Work.leading_article,
                author_agg.c.author_names,
                LabelSet.id.label("labelset_id"),
                LabelSet.hue_origin,
                LabelSet.checked,
                LabelSet.min_age,
                LabelSet.max_age,
                LabelSet.recommend_status,
                func.coalesce(cf.c.school_count, 0).label("school_count"),
                func.coalesce(cf.c.collection_frequency, 0).label(
                    "collection_frequency"
                ),
                func.coalesce(review_agg.c.review_count, 0).label("review_count"),
                review_agg.c.reviewer_names,
            )
            .select_from(Work)
            .outerjoin(LabelSet, LabelSet.work_id == Work.id)
            .outerjoin(cf, cf.c.work_id == Work.id)
            .outerjoin(
                review_agg,
                review_agg.c.entity_id == cast(LabelSet.id, String),
            )
            .outerjoin(author_agg, author_agg.c.work_id == Work.id)
        )

        # Status filtering
        if status == "unchecked":
            stmt = stmt.where(
                (LabelSet.checked.is_(None)) | (LabelSet.checked == False)  # noqa: E712
            )
        elif status == "ai_labelled":
            stmt = stmt.where(LabelSet.hue_origin.in_(AI_ORIGINS))
        elif status == "human_reviewed":
            stmt = stmt.where(LabelSet.hue_origin == LabelOrigin.HUMAN)

        # Minimum school count filter
        if min_school_count > 0:
            stmt = stmt.where(func.coalesce(cf.c.school_count, 0) >= min_school_count)

        # Get total count before pagination
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = db.execute(count_stmt).scalar_one()

        # Order and paginate
        stmt = stmt.order_by(
            func.coalesce(cf.c.school_count, 0).desc(),
            Work.id,
        )
        stmt = stmt.offset(skip).limit(limit)

        rows = db.execute(stmt).all()

        # Get hue_primary_key via the association table
        labelset_ids = [r.labelset_id for r in rows if r.labelset_id is not None]
        hue_map = {}
        if labelset_ids:
            hue_stmt = (
                select(LabelSetHue.labelset_id, Hue.key)
                .join(Hue, LabelSetHue.hue_id == Hue.id)
                .where(
                    LabelSetHue.labelset_id.in_(labelset_ids),
                    LabelSetHue.ordinal == Ordinal.PRIMARY,
                )
            )
            for hue_row in db.execute(hue_stmt).all():
                hue_map[hue_row.labelset_id] = hue_row.key

        items = []
        for row in rows:
            items.append(
                {
                    "work_id": row.work_id,
                    "title": row.title,
                    "subtitle": row.subtitle,
                    "leading_article": row.leading_article,
                    "authors": [name.strip() for name in (row.author_names or [])],
                    "labelset_id": row.labelset_id,
                    "hue_primary_key": hue_map.get(row.labelset_id),
                    "hue_origin": row.hue_origin,
                    "checked": row.checked,
                    "min_age": row.min_age,
                    "max_age": row.max_age,
                    "recommend_status": row.recommend_status,
                    "school_count": row.school_count,
                    "collection_frequency": row.collection_frequency,
                    "review_count": row.review_count,
                    "reviewer_names": [
                        n for n in (row.reviewer_names or []) if n is not None
                    ],
                }
            )

        return items, total

    def get_review_stats(self, db: Session) -> dict:
        """Get aggregate review statistics for the dashboard."""
        total_works = db.execute(select(func.count(Work.id))).scalar_one()

        labelset_stats = db.execute(
            select(
                func.count(LabelSet.id).label("total"),
                func.count(
                    case((LabelSet.checked == True, 1))  # noqa: E712
                ).label("checked"),
                func.count(
                    case(
                        (
                            (LabelSet.checked.is_(None)) | (LabelSet.checked == False),  # noqa: E712
                            1,
                        )
                    )
                ).label("unchecked"),
                func.count(case((LabelSet.hue_origin == LabelOrigin.HUMAN, 1))).label(
                    "human_hued"
                ),
                func.count(case((LabelSet.hue_origin.in_(AI_ORIGINS), 1))).label(
                    "ai_hued"
                ),
                func.count(case((LabelSet.hue_origin.is_(None), 1))).label("no_hue"),
            )
        ).one()

        total_reviews = db.execute(
            select(func.count(Review.id)).where(
                Review.reviewable_type == ReviewableType.LABELSET
            )
        ).scalar_one()

        # Top reviewers leaderboard
        reviewer_stmt = (
            select(
                Review.reviewer_user_id,
                User.name,
                func.count(Review.id).label("review_count"),
            )
            .join(User, Review.reviewer_user_id == User.id)
            .where(Review.reviewable_type == ReviewableType.LABELSET)
            .group_by(Review.reviewer_user_id, User.name)
            .order_by(func.count(Review.id).desc())
            .limit(20)
        )
        top_reviewers = [
            {
                "user_id": row.reviewer_user_id,
                "name": row.name or "Unknown",
                "review_count": row.review_count,
            }
            for row in db.execute(reviewer_stmt).all()
        ]

        return {
            "total_works": total_works,
            "works_with_labelset": labelset_stats.total,
            "works_checked": labelset_stats.checked,
            "works_unchecked": labelset_stats.unchecked,
            "works_human_hued": labelset_stats.human_hued,
            "works_ai_hued": labelset_stats.ai_hued,
            "works_no_hue": labelset_stats.no_hue,
            "total_reviews": total_reviews,
            "top_reviewers": top_reviewers,
        }


review_repository = ReviewRepositoryImpl()

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer, joinedload, raiseload, selectinload

from app.models import Edition, LabelSet, Work


@dataclass(frozen=True)
class RecommendationCandidate:
    work_id: int
    labelset_id: int
    cover_edition_isbn: str


class RecommendationRepository:
    async def load_ranked_candidates(
        self,
        session: AsyncSession,
        candidates: Sequence[RecommendationCandidate],
    ) -> list[tuple[Work, Edition, LabelSet]]:
        """Load response fields in rank order, skipping stale missing candidates."""
        if not candidates:
            return []
        works = (
            await session.scalars(
                select(Work)
                .where(Work.id.in_([row.work_id for row in candidates]))
                .options(raiseload("*"), selectinload(Work.authors))
            )
        ).all()
        editions = (
            await session.scalars(
                select(Edition)
                .where(Edition.isbn.in_([row.cover_edition_isbn for row in candidates]))
                .options(
                    raiseload("*"),
                    joinedload(Edition.work).raiseload("*"),
                    defer(Edition.collection_count, raiseload=True),
                )
            )
        ).all()
        labelsets = (
            await session.scalars(
                select(LabelSet)
                .where(LabelSet.id.in_([row.labelset_id for row in candidates]))
                .options(
                    raiseload("*"),
                    selectinload(LabelSet.hues),
                    selectinload(LabelSet.reading_abilities),
                )
            )
        ).all()
        works_by_id = {work.id: work for work in works}
        editions_by_isbn = {edition.isbn: edition for edition in editions}
        labelsets_by_id = {labelset.id: labelset for labelset in labelsets}
        result = []
        for candidate in candidates:
            work = works_by_id.get(candidate.work_id)
            edition = editions_by_isbn.get(candidate.cover_edition_isbn)
            labelset = labelsets_by_id.get(candidate.labelset_id)
            if work is not None and edition is not None and labelset is not None:
                result.append((work, edition, labelset))
        return result


recommendation_repository = RecommendationRepository()

"""
School repository - domain-focused data access for School domain.

Replaces the generic CRUDSchool class with proper repository pattern.
"""

from abc import ABC, abstractmethod
from typing import Optional, Sequence

from fastapi import HTTPException
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, selectinload
from structlog import get_logger

from app.models import ClassGroup, Event, School, Student, Subscription
from app.models.collection import Collection
from app.models.educator import Educator
from app.models.school_admin import SchoolAdmin
from app.models.user import User, UserAccountType
from app.schemas.school import SchoolCreateIn, SchoolPatchOptions

logger = get_logger()

# word_similarity threshold for the `%>` operator used in fuzzy school-name
# search. Lower than the pg_trgm default (0.6) so common typos and
# transpositions (e.g. "somerfeild" -> "Somerfield") are still recalled, while
# still letting the GIN trigram index serve the query.
SCHOOL_SEARCH_WORD_SIMILARITY_THRESHOLD = 0.4


class SchoolRepository(ABC):
    """Repository interface for School domain operations."""

    @abstractmethod
    def get(self, db: Session, id: int) -> Optional[School]:
        """Get a school by its primary key ID."""
        pass

    @abstractmethod
    async def aget(self, db: AsyncSession, id: int) -> Optional[School]:
        """Async version of get."""
        pass

    @abstractmethod
    def get_by_id_or_404(self, db: Session, id: int) -> School:
        """Get a school by ID or raise 404."""
        pass

    @abstractmethod
    def get_by_wriveted_id(self, db: Session, wriveted_id: str) -> Optional[School]:
        """Get a school by its wriveted identifier."""
        pass

    @abstractmethod
    def get_by_wriveted_id_or_404(self, db: Session, wriveted_id: str) -> School:
        """Get a school by wriveted ID or raise 404."""
        pass

    @abstractmethod
    async def aget_by_wriveted_id_or_404(
        self, db: AsyncSession, wriveted_id: str
    ) -> School:
        """Async version of get_by_wriveted_id_or_404 with eager loading."""
        pass

    @abstractmethod
    def get_by_official_id_or_404(
        self, db: Session, country_code: str, official_id: str
    ) -> School:
        """Get a school by country code and official identifier or raise 404."""
        pass

    @abstractmethod
    def get_all_query_with_optional_filters(
        self,
        db: Session,
        country_code: Optional[str] = None,
        state: Optional[str] = None,
        postcode: Optional[str] = None,
        query_string: Optional[str] = None,
        is_active: Optional[bool] = None,
        is_collection_connected: Optional[bool] = None,
        has_active_subscription: Optional[bool] = None,
        official_identifier: Optional[str] = None,
    ):
        """Build a query with optional filters for schools."""
        pass

    @abstractmethod
    async def get_all_with_optional_filters(
        self,
        db: AsyncSession,
        country_code: Optional[str] = None,
        state: Optional[str] = None,
        postcode: Optional[str] = None,
        query_string: Optional[str] = None,
        is_active: Optional[bool] = None,
        is_collection_connected: Optional[bool] = None,
        has_active_subscription: Optional[bool] = None,
        official_identifier: Optional[str] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> Sequence[School]:
        """Get schools with optional filters and pagination."""
        pass

    @abstractmethod
    def create(
        self, db: Session, obj_in: SchoolCreateIn, commit: bool = True
    ) -> School:
        """Create a new school."""
        pass

    @abstractmethod
    def update(
        self,
        db: Session,
        db_obj: School,
        obj_in: SchoolPatchOptions,
        commit: bool = True,
    ) -> School:
        """Update an existing school."""
        pass

    @abstractmethod
    async def aupdate(
        self,
        db: AsyncSession,
        db_obj: School,
        obj_in: SchoolPatchOptions,
        commit: bool = True,
    ) -> School:
        """Async version of update."""
        pass

    @abstractmethod
    def remove(self, db: Session, obj_in: School) -> School:
        """Delete a school and all related data."""
        pass

    @abstractmethod
    def apply_pagination(self, query, skip: int = 0, limit: int = 100):
        """Apply pagination to a query."""
        pass


class SchoolRepositoryImpl(SchoolRepository):
    """Implementation of SchoolRepository."""

    def get(self, db: Session, id: int) -> Optional[School]:
        """Get a school by its primary key ID."""
        return db.get(School, id)

    async def aget(self, db: AsyncSession, id: int) -> Optional[School]:
        """Async version of get."""
        return await db.get(School, id)

    def get_by_id_or_404(self, db: Session, id: int) -> School:
        """Get a school by ID or raise 404."""
        query = select(School).where((School.id) == (id))
        try:
            return db.execute(query).scalar_one()
        except NoResultFound:
            raise HTTPException(
                status_code=404, detail=f"School with id {id} not found."
            )

    def get_by_wriveted_id(self, db: Session, wriveted_id: str) -> Optional[School]:
        """Get a school by its wriveted identifier."""
        query = select(School).where(School.wriveted_identifier == wriveted_id)
        try:
            return db.execute(query).scalar_one()
        except NoResultFound:
            return None

    def get_by_wriveted_id_or_404(self, db: Session, wriveted_id: str) -> School:
        """Get a school by wriveted ID or raise 404."""
        query = select(School).where(School.wriveted_identifier == wriveted_id)
        try:
            return db.execute(query).scalar_one()
        except NoResultFound:
            raise HTTPException(
                status_code=404,
                detail=f"School with wriveted_id {wriveted_id} not found.",
            )

    async def aget_by_wriveted_id_or_404(
        self, db: AsyncSession, wriveted_id: str
    ) -> School:
        """Async version of get_by_wriveted_id_or_404 with eager loading."""
        query = (
            select(School)
            .where(School.wriveted_identifier == wriveted_id)
            .options(selectinload(School.admins))
            .options(selectinload(School.country))
            .options(selectinload(School.subscription))
            .options(selectinload(School.booklists))
            .options(selectinload(School.collection))
        )
        try:
            return (await db.execute(query)).scalar_one()
        except NoResultFound:
            raise HTTPException(
                status_code=404,
                detail=f"School with wriveted_id {wriveted_id} not found.",
            )

    def get_by_official_id_or_404(
        self, db: Session, country_code: str, official_id: str
    ) -> School:
        """Get a school by country code and official identifier or raise 404."""
        query = (
            select(School)
            .where(School.country_code == country_code.upper())
            .where(School.official_identifier == official_id)
        )
        try:
            return db.execute(query).scalar_one()
        except NoResultFound:
            raise HTTPException(
                status_code=404,
                detail=f"School with id {official_id} in {country_code} not found.",
            )

    def get_all_query_with_optional_filters(
        self,
        db: Session,
        country_code: Optional[str] = None,
        state: Optional[str] = None,
        postcode: Optional[str] = None,
        query_string: Optional[str] = None,
        is_active: Optional[bool] = None,
        is_collection_connected: Optional[bool] = None,
        has_active_subscription: Optional[bool] = None,
        official_identifier: Optional[str] = None,
    ):
        """Build a query with optional filters for schools."""
        from sqlalchemy import case, func, or_

        school_query = select(School).options(
            selectinload(School.subscription).selectinload(Subscription.product),
        )

        if country_code is not None:
            school_query = school_query.where(School.country_code == country_code)
        if state is not None:
            school_query = school_query.where(
                School.info["location", "state"].as_string() == state
            )
        if postcode is not None:
            school_query = school_query.where(
                School.info["location", "postcode"].as_string() == postcode
            )

        relevance_ordering = None
        if query_string is not None and query_string.strip():
            q = query_string.strip().lower()
            name_lower = func.lower(School.name)

            # word_similarity finds the query as a fuzzy substring within the
            # (often long) school name; similarity scores the whole string.
            # GREATEST keeps the stronger of the two signals.
            word_sim = func.word_similarity(q, name_lower)
            full_sim = func.similarity(name_lower, q)
            fuzzy_score = func.greatest(word_sim, full_sim)

            # Boost exact prefix/substring hits above purely fuzzy matches so a
            # clean partial like "Somer" always outranks a typo-distance match.
            relevance = fuzzy_score + case(
                (name_lower.like(q + "%"), 1.0),
                (name_lower.like("%" + q + "%"), 0.5),
                else_=0.0,
            )

            # On a tie (common when two names share the matched word), prefer the
            # name whose whole string is closest to the query, which favours the
            # shorter, more specific school over a longer name that merely
            # contains the same word.
            relevance_ordering = [relevance.desc(), full_sim.desc()]

            # Recall: keep any substring hit, plus fuzzy hits via the word
            # similarity operator (catches typos like "Sommerfield" /
            # "Somerfeild"). Both the LIKE and the `%>` operator are served by
            # the GIN trigram index (idx_schools_name_trgm) as a BitmapOr, and
            # `%>` honours pg_trgm.word_similarity_threshold, which the async
            # caller lowers to SCHOOL_SEARCH_WORD_SIMILARITY_THRESHOLD per
            # transaction.
            school_query = school_query.where(
                or_(
                    name_lower.like("%" + q + "%"),
                    name_lower.op("%>")(q),
                )
            )
        if is_active is not None:
            school_query = school_query.where(
                School.state == ("active" if is_active else "inactive")
            )
        if is_collection_connected is not None:
            school_query = school_query.join(Collection).where(
                Collection.book_count > 0
            )
        if has_active_subscription is not None:
            school_query = school_query.join(Subscription).where(
                Subscription.is_active == has_active_subscription
            )
        if official_identifier is not None:
            school_query = school_query.where(
                School.official_identifier == official_identifier
            )

        school_query = (
            school_query.options(selectinload(School.country))
            .options(selectinload(School.admins))
            .options(selectinload(School.collection))
        )

        if relevance_ordering is not None:
            school_query = school_query.order_by(
                *relevance_ordering, School.created_at.desc()
            )
        else:
            school_query = school_query.order_by(School.created_at.desc())

        return school_query

    async def get_all_with_optional_filters(
        self,
        db: AsyncSession,
        country_code: Optional[str] = None,
        state: Optional[str] = None,
        postcode: Optional[str] = None,
        query_string: Optional[str] = None,
        is_active: Optional[bool] = None,
        is_collection_connected: Optional[bool] = None,
        has_active_subscription: Optional[bool] = None,
        official_identifier: Optional[str] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> Sequence[School]:
        """Get schools with optional filters and pagination."""
        query = self.apply_pagination(
            self.get_all_query_with_optional_filters(
                db,
                country_code=country_code,
                state=state,
                postcode=postcode,
                query_string=query_string,
                is_active=is_active,
                is_collection_connected=is_collection_connected,
                has_active_subscription=has_active_subscription,
                official_identifier=official_identifier,
            ),
            skip=skip,
            limit=limit,
        )
        if query_string is not None and query_string.strip():
            # Scope the lowered word_similarity threshold to this transaction so
            # the fuzzy `%>` recall in the search query stays index-served
            # without leaking the setting onto pooled connections.
            await db.execute(
                text(
                    "SET LOCAL pg_trgm.word_similarity_threshold = "
                    f"{SCHOOL_SEARCH_WORD_SIMILARITY_THRESHOLD}"
                )
            )
        return (await db.execute(query)).scalars().all()

    def create(
        self, db: Session, obj_in: SchoolCreateIn, commit: bool = True
    ) -> School:
        """Create a new school."""
        obj_in_data = obj_in.model_dump()
        db_obj = School(**obj_in_data)
        db.add(db_obj)
        if commit:
            db.commit()
            db.refresh(db_obj)
        return db_obj

    def update(
        self,
        db: Session,
        db_obj: School,
        obj_in: SchoolPatchOptions,
        commit: bool = True,
    ) -> School:
        """Update an existing school."""
        update_data = obj_in.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(db_obj, field, value)
        db.add(db_obj)
        if commit:
            db.commit()
            db.refresh(db_obj)
        return db_obj

    async def aupdate(
        self,
        db: AsyncSession,
        db_obj: School,
        obj_in: SchoolPatchOptions,
        commit: bool = True,
    ) -> School:
        """Async version of update."""
        update_data = obj_in.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(db_obj, field, value)
        db.add(db_obj)
        if commit:
            await db.commit()
            await db.refresh(db_obj)
        return db_obj

    def remove(self, db: Session, obj_in: School) -> School:
        """Delete a school and all related data."""
        stmt = delete(Collection).where(
            Collection.school_id == obj_in.wriveted_identifier
        )
        db.execute(stmt)

        demote_students = (
            update(User)
            .where(
                User.id.in_(select(Student.id).where(Student.school_id == obj_in.id))
            )
            .values(type=UserAccountType.PUBLIC, is_active=False)
            .execution_options(synchronize_session=False)
        )

        db.execute(demote_students)

        delete_students = delete(Student).where(Student.school_id == obj_in.id)
        db.execute(delete_students)

        stmt = delete(ClassGroup).where(
            ClassGroup.school_id == obj_in.wriveted_identifier
        )
        db.execute(stmt)

        demote_schooladmins = (
            update(User)
            .where(
                User.id.in_(
                    select(SchoolAdmin.id).where(SchoolAdmin.school_id == obj_in.id)
                )
            )
            .values(type=UserAccountType.PUBLIC, is_active=False)
            .execution_options(synchronize_session=False)
        )

        db.execute(demote_schooladmins)

        demote_educators = (
            update(User)
            .where(
                User.id.in_(select(Educator.id).where(Educator.school_id == obj_in.id))
            )
            .values(type=UserAccountType.PUBLIC, is_active=False)
            .execution_options(synchronize_session=False)
        )
        db.execute(demote_educators)

        stmt = delete(Event).where(Event.school_id == obj_in.id)
        db.execute(stmt)
        logger.info("Deleting database objects related to the school")
        db.commit()
        logger.info("Deleting a school", wriveted_id=obj_in.wriveted_identifier)

        db.execute(
            delete(School)
            .where(School.wriveted_identifier == obj_in.wriveted_identifier)
            .execution_options(synchronize_session=False)
        )
        db.commit()
        return obj_in

    def apply_pagination(self, query, skip: int = 0, limit: int = 100):
        """Apply pagination to a query."""
        return query.offset(skip).limit(limit)


# Singleton instance
school_repository = SchoolRepositoryImpl()

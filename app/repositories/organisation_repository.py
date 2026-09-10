from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, literal, or_, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.collection import Collection
from app.models.collection_item import CollectionItem
from app.models.country import Country
from app.models.edition import Edition
from app.models.educator import Educator
from app.models.organisation import (
    LibraryMembership,
    Organisation,
    OrganisationMembership,
    OrganisationSubscription,
)
from app.models.school import School
from app.models.subscription import Subscription, SubscriptionType
from app.models.user import User, UserAccountType
from app.models.work import Work


@dataclass(frozen=True)
class LibraryScope:
    user_id: UUID
    home_school_id: int | None
    unrestricted: bool = False


@dataclass(frozen=True)
class HoldingUpdate:
    edition_isbn: str
    copies_total: int
    title: str | None


class OrganisationRepository:
    """Compatibility persistence for organisation-owned libraries; never commits."""

    def _library_scope(self, scope: LibraryScope):
        return or_(
            School.organisation_id.in_(
                select(OrganisationMembership.organisation_id).where(
                    OrganisationMembership.user_id == scope.user_id
                )
            ),
            School.id.in_(
                select(LibraryMembership.school_id).where(
                    LibraryMembership.user_id == scope.user_id
                )
            ),
            School.id == scope.home_school_id
            if scope.home_school_id is not None
            else False,
        )

    async def list_organisations(
        self, db: AsyncSession, scope: LibraryScope, *, skip: int = 0, limit: int = 100
    ) -> tuple[list[Organisation], int]:
        query = select(Organisation)
        if not scope.unrestricted:
            query = query.where(
                or_(
                    Organisation.id.in_(
                        select(OrganisationMembership.organisation_id).where(
                            OrganisationMembership.user_id == scope.user_id
                        )
                    ),
                    Organisation.id.in_(
                        select(School.organisation_id).where(self._library_scope(scope))
                    ),
                )
            )
        total = await db.scalar(select(func.count()).select_from(query.subquery()))
        return list(
            await db.scalars(
                query.order_by(Organisation.name, Organisation.id)
                .offset(skip)
                .limit(limit)
            )
        ), total

    async def list_libraries(
        self,
        db: AsyncSession,
        scope: LibraryScope,
        *,
        skip: int = 0,
        limit: int = 50,
        q: str | None = None,
        organisation_uuid: UUID | None = None,
        standalone: bool = False,
        country_code: str | None = None,
        has_catalogue: bool | None = None,
    ) -> tuple[list[School], int]:
        query = select(School)
        if not scope.unrestricted:
            query = query.where(self._library_scope(scope))
        if q:
            query = query.where(School.name.ilike(f"%{q}%"))
        if organisation_uuid is not None:
            query = query.where(School.organisation_id == organisation_uuid)
        if standalone:
            query = query.where(School.organisation_id.is_(None))
        if country_code is not None:
            query = query.where(School.country_code == country_code)
        if has_catalogue is not None:
            catalogue_exists = (
                select(Collection.id)
                .where(Collection.school_id == School.school_uuid)
                .exists()
            )
            query = query.where(
                catalogue_exists if has_catalogue else ~catalogue_exists
            )
        total = await db.scalar(select(func.count()).select_from(query.subquery()))
        libraries = await db.scalars(
            query.order_by(School.name, School.id).offset(skip).limit(limit)
        )
        return list(libraries), total

    async def get_organisation(
        self, db: AsyncSession, organisation_uuid: UUID, *, lock: bool = False
    ) -> Organisation | None:
        query = select(Organisation).where(Organisation.id == organisation_uuid)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return await db.scalar(query)

    async def get_library(
        self, db: AsyncSession, library_uuid: UUID, *, lock: bool = False
    ) -> School | None:
        query = select(School).where(School.school_uuid == library_uuid)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return await db.scalar(query)

    async def country_exists(self, db: AsyncSession, country_code: str) -> bool:
        return await db.get(Country, country_code) is not None

    async def get_collection(
        self,
        db: AsyncSession,
        library_uuid: UUID,
        collection_uuid: UUID,
        *,
        lock: bool = False,
    ) -> Collection | None:
        query = select(Collection).where(
            Collection.id == collection_uuid, Collection.school_id == library_uuid
        )
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return await db.scalar(query)

    async def count_collections(self, db: AsyncSession, library_uuid: UUID) -> int:
        return await db.scalar(
            select(func.count())
            .select_from(Collection)
            .where(Collection.school_id == library_uuid)
        )

    async def collection_summaries(
        self, db: AsyncSession, library_uuids: Sequence[UUID]
    ) -> list[dict]:
        rows = await db.execute(
            select(
                Collection.school_id.label("library_uuid"),
                Collection.id,
                Collection.name,
                Collection.is_default,
                Collection.book_count,
            )
            .where(Collection.school_id.in_(library_uuids))
            .order_by(Collection.is_default.desc(), Collection.name, Collection.id)
        )
        return [dict(row) for row in rows.mappings()]

    async def list_holdings(
        self,
        db: AsyncSession,
        collection_uuid: UUID,
        *,
        skip: int,
        limit: int,
        q: str | None,
    ) -> tuple[list[dict], int]:
        title = func.coalesce(
            CollectionItem.info["title"].astext,
            Edition.edition_title,
            Work.title,
            Edition.title,
        )
        query = (
            select(
                CollectionItem.id,
                CollectionItem.edition_isbn,
                title.label("title"),
                CollectionItem.copies_total,
            )
            .outerjoin(Edition, Edition.isbn == CollectionItem.edition_isbn)
            .outerjoin(Work, Work.id == Edition.work_id)
            .where(CollectionItem.collection_id == collection_uuid)
        )
        if q:
            query = query.where(
                or_(title.ilike(f"%{q}%"), CollectionItem.edition_isbn.ilike(f"%{q}%"))
            )
        total = await db.scalar(select(func.count()).select_from(query.subquery()))
        rows = await db.execute(
            query.order_by(CollectionItem.id).offset(skip).limit(limit)
        )
        return [dict(row) for row in rows.mappings()], total

    async def lock_holdings(
        self, db: AsyncSession, collection_uuid: UUID, isbns: Sequence[str]
    ) -> dict[str, HoldingUpdate]:
        rows = await db.execute(
            select(
                CollectionItem.edition_isbn,
                CollectionItem.copies_total,
                CollectionItem.info,
            )
            .where(
                CollectionItem.collection_id == collection_uuid,
                CollectionItem.edition_isbn.in_(isbns),
            )
            .order_by(CollectionItem.edition_isbn)
            .with_for_update()
        )
        return {
            row.edition_isbn: HoldingUpdate(
                row.edition_isbn, row.copies_total, (row.info or {}).get("title")
            )
            for row in rows
        }

    async def count_holdings(self, db: AsyncSession, collection_uuid: UUID) -> int:
        return await db.scalar(
            select(func.count())
            .select_from(CollectionItem)
            .where(CollectionItem.collection_id == collection_uuid)
        )

    async def upsert_holdings(
        self, db: AsyncSession, collection_uuid: UUID, items: Sequence[HoldingUpdate]
    ) -> None:
        ordered = sorted(items, key=lambda item: item.edition_isbn)
        await db.execute(
            insert(Edition)
            .values([{"isbn": item.edition_isbn} for item in ordered])
            .on_conflict_do_nothing(index_elements=[Edition.isbn])
        )
        for with_title in (False, True):
            values = [
                {
                    "collection_id": collection_uuid,
                    "edition_isbn": item.edition_isbn,
                    "copies_total": item.copies_total,
                    "copies_available": item.copies_total,
                    "info": {"title": item.title} if item.title else {},
                }
                for item in ordered
                if bool(item.title) == with_title
            ]
            if not values:
                continue
            statement = insert(CollectionItem).values(values)
            updates = {
                "copies_total": statement.excluded.copies_total,
                "copies_available": func.least(
                    statement.excluded.copies_total,
                    func.greatest(
                        statement.excluded.copies_total
                        - func.greatest(
                            CollectionItem.copies_total
                            - CollectionItem.copies_available,
                            0,
                        ),
                        0,
                    ),
                ),
                "updated_at": func.now(),
            }
            if with_title:
                updates["info"] = func.coalesce(
                    CollectionItem.info, literal({}, type_=JSONB)
                ).op("||")(statement.excluded.info)
            await db.execute(
                statement.on_conflict_do_update(
                    constraint="uq_collection_items_collection_id_edition_isbn",
                    set_=updates,
                )
            )
        await db.execute(
            Collection.__table__.update()
            .where(Collection.id == collection_uuid)
            .values(updated_at=func.now())
        )

    async def get_user(self, db: AsyncSession, user_uuid: UUID) -> User | None:
        return await db.get(User, user_uuid)

    async def find_user_by_email(self, db: AsyncSession, email: str) -> User | None:
        matches = list(
            await db.scalars(
                select(User).where(func.lower(User.email) == email.lower()).limit(2)
            )
        )
        return matches[0] if len(matches) == 1 else None

    async def organisation_membership(
        self, db: AsyncSession, organisation_uuid: UUID, user_uuid: UUID
    ) -> OrganisationMembership | None:
        return await db.get(OrganisationMembership, (organisation_uuid, user_uuid))

    async def library_membership(
        self, db: AsyncSession, school_id: int, user_uuid: UUID
    ) -> LibraryMembership | None:
        return await db.get(LibraryMembership, (school_id, user_uuid))

    async def managed_organisation_ids(
        self, db: AsyncSession, user_uuid: UUID
    ) -> set[UUID]:
        return set(
            await db.scalars(
                select(OrganisationMembership.organisation_id).where(
                    OrganisationMembership.user_id == user_uuid
                )
            )
        )

    async def library_roles(
        self, db: AsyncSession, user_uuid: UUID, school_ids: Sequence[int]
    ) -> dict[int, str]:
        rows = await db.execute(
            select(LibraryMembership.school_id, LibraryMembership.role).where(
                LibraryMembership.user_id == user_uuid,
                LibraryMembership.school_id.in_(school_ids),
            )
        )
        return dict(rows.all())

    async def organisation_members(
        self,
        db: AsyncSession,
        organisation_uuid: UUID,
        *,
        skip: int = 0,
        limit: int = 100,
    ) -> tuple[list[dict], int]:
        query = (
            select(User.id, User.name)
            .join(OrganisationMembership, OrganisationMembership.user_id == User.id)
            .where(OrganisationMembership.organisation_id == organisation_uuid)
        )
        total = await db.scalar(select(func.count()).select_from(query.subquery()))
        rows = await db.execute(
            query.order_by(User.name, User.id).offset(skip).limit(limit)
        )
        return [dict(row) for row in rows.mappings()], total

    async def library_members(
        self, db: AsyncSession, school_id: int, *, skip: int = 0, limit: int = 100
    ) -> tuple[list[dict], int]:
        query = (
            select(User.id, User.name, LibraryMembership.role)
            .join(LibraryMembership, LibraryMembership.user_id == User.id)
            .where(LibraryMembership.school_id == school_id)
        )
        total = await db.scalar(select(func.count()).select_from(query.subquery()))
        rows = await db.execute(
            query.order_by(User.name, User.id).offset(skip).limit(limit)
        )
        return [dict(row) for row in rows.mappings()], total

    async def grant_organisation_membership(
        self, db: AsyncSession, organisation_uuid: UUID, user_uuid: UUID
    ) -> None:
        await db.execute(
            insert(OrganisationMembership)
            .values(organisation_id=organisation_uuid, user_id=user_uuid)
            .on_conflict_do_nothing()
        )

    async def grant_library_membership(
        self, db: AsyncSession, school_id: int, user_uuid: UUID, role: str
    ) -> None:
        statement = insert(LibraryMembership).values(
            school_id=school_id, user_id=user_uuid, role=role
        )
        await db.execute(
            statement.on_conflict_do_update(
                index_elements=[LibraryMembership.school_id, LibraryMembership.user_id],
                set_={"role": role},
            )
        )

    async def active_organisation_manager_count(
        self,
        db: AsyncSession,
        organisation_uuid: UUID,
        eligible_roles: set[UserAccountType],
    ) -> int:
        return await db.scalar(
            select(func.count())
            .select_from(OrganisationMembership)
            .join(User, User.id == OrganisationMembership.user_id)
            .where(
                OrganisationMembership.organisation_id == organisation_uuid,
                User.is_active.is_(True),
                User.type.in_(eligible_roles),
            )
        )

    async def is_home_library_administrator(
        self, db: AsyncSession, school_id: int, user_uuid: UUID
    ) -> bool:
        return await db.scalar(
            select(
                select(Educator.id)
                .where(
                    Educator.id == user_uuid,
                    Educator.school_id == school_id,
                    User.type == UserAccountType.SCHOOL_ADMIN,
                    User.is_active.is_(True),
                )
                .exists()
            )
        )

    async def has_other_library_manager(
        self,
        db: AsyncSession,
        school_id: int,
        excluding_user: UUID,
        eligible_roles: set[UserAccountType],
    ) -> bool:
        direct_manager = select(LibraryMembership.user_id).where(
            LibraryMembership.school_id == school_id,
            LibraryMembership.role == "manager",
        )
        home_admin = select(Educator.id).where(Educator.school_id == school_id)
        return await db.scalar(
            select(
                select(User.id)
                .where(
                    User.id != excluding_user,
                    User.is_active.is_(True),
                    User.type.in_(eligible_roles),
                    or_(
                        User.id.in_(direct_manager),
                        (User.type == UserAccountType.SCHOOL_ADMIN)
                        & User.id.in_(home_admin),
                    ),
                )
                .exists()
            )
        )

    async def entitlement_evidence(
        self, db: AsyncSession, organisation_ids: set[UUID]
    ) -> tuple[dict[UUID, int], dict[UUID, list[Subscription]]]:
        counts = dict(
            (
                await db.execute(
                    select(School.organisation_id, func.count())
                    .where(School.organisation_id.in_(organisation_ids))
                    .group_by(School.organisation_id)
                )
            ).all()
        )
        rows = await db.execute(
            select(OrganisationSubscription.organisation_id, Subscription)
            .join(
                Subscription,
                Subscription.id == OrganisationSubscription.subscription_id,
            )
            .where(
                OrganisationSubscription.organisation_id.in_(organisation_ids),
                Subscription.type.in_(
                    [SubscriptionType.SCHOOL, SubscriptionType.LIBRARY]
                ),
                Subscription.parent_id.is_(None),
            )
        )
        subscriptions: dict[UUID, list[Subscription]] = {}
        for organisation_id, subscription in rows:
            subscriptions.setdefault(organisation_id, []).append(subscription)
        return counts, subscriptions


organisation_repository = OrganisationRepository()

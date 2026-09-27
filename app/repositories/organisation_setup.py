from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.educator import Educator
from app.models.organisation import (
    LibraryMembership,
    Organisation,
    OrganisationSubscription,
)
from app.models.subscription import Subscription, SubscriptionType
from app.models.user import User, UserAccountType

ELIGIBLE_MANAGER_TYPES = [UserAccountType.SCHOOL_ADMIN, UserAccountType.EDUCATOR]


async def library_subscriptions(
    db: AsyncSession, library_uuid: UUID, *, lock: bool = False
) -> list[Subscription]:
    query = (
        select(Subscription)
        .where(
            Subscription.school_id == library_uuid,
            Subscription.parent_id.is_(None),
            Subscription.type.in_([SubscriptionType.SCHOOL, SubscriptionType.LIBRARY]),
        )
        .order_by(Subscription.id)
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return list(await db.scalars(query))


async def subscription_owner(
    db: AsyncSession, subscription_id: str
) -> OrganisationSubscription | None:
    return await db.get(OrganisationSubscription, subscription_id)


def _eligible_managers(school_id: int):
    """People who already manage this library and can therefore manage a group.

    Home staff of the school, plus anyone holding an explicit library manager
    grant. Reviewers and cataloguers are read or catalogue scoped, so promoting
    one straight to organisation manager would widen access beyond what the
    person performing setup could grant here.
    """
    return select(User).where(
        User.is_active.is_(True),
        User.type.in_(ELIGIBLE_MANAGER_TYPES),
        or_(
            User.id.in_(select(Educator.id).where(Educator.school_id == school_id)),
            User.id.in_(
                select(LibraryMembership.user_id).where(
                    LibraryMembership.school_id == school_id,
                    LibraryMembership.role == "manager",
                )
            ),
        ),
    )


async def eligible_manager_page(
    db: AsyncSession, school_id: int, q: str, skip: int, limit: int
) -> tuple[list[User], int]:
    query = _eligible_managers(school_id)
    if q:
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.where(
            or_(
                User.name.ilike(f"%{escaped}%", escape="\\"),
                User.email.ilike(f"%{escaped}%", escape="\\"),
            )
        )
    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    rows = list(
        await db.scalars(
            query.order_by(func.lower(User.name), User.id).offset(skip).limit(limit)
        )
    )
    return rows, total


async def eligible_manager_count(db: AsyncSession, school_id: int) -> int:
    return await db.scalar(
        select(func.count()).select_from(_eligible_managers(school_id).subquery())
    )


async def selected_managers(
    db: AsyncSession, school_id: int, user_ids: list[UUID]
) -> dict[UUID, User]:
    rows = await db.scalars(
        _eligible_managers(school_id).where(User.id.in_(user_ids)).with_for_update()
    )
    return {user.id: user for user in rows}


async def create_organisation(
    db: AsyncSession, name: str, kind: str, subscription_id: str, actor_id: UUID
) -> Organisation:
    organisation = Organisation(name=name, kind=kind)
    db.add(organisation)
    await db.flush()
    db.add(
        OrganisationSubscription(
            organisation_id=organisation.id,
            subscription_id=subscription_id,
            linked_by=actor_id,
        )
    )
    return organisation

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, func, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import with_polymorphic

from app.models.educator import Educator
from app.models.event_outbox import EventOutbox
from app.models.organisation import LibraryMembership, OrganisationMembership
from app.models.school_admin import SchoolAdmin
from app.models.user import User, UserAccountType


async def actor_account(db: AsyncSession, user_id: UUID) -> User | None:
    account = with_polymorphic(User, "*")
    return await db.scalar(
        select(account)
        .where(account.id == user_id)
        .execution_options(populate_existing=True)
    )


async def person(db: AsyncSession, user_id: UUID, *, lock: bool = True):
    query = (
        select(
            User.id,
            User.name,
            User.email,
            User.type,
            User.is_active,
            User.last_login_at,
            Educator.__table__.c.school_id,
        )
        .outerjoin(Educator.__table__, Educator.__table__.c.id == User.id)
        .where(User.id == user_id)
    )
    if lock:
        query = query.with_for_update(of=User)
    return (await db.execute(query)).mappings().one_or_none()


async def find_email(db: AsyncSession, email: str):
    return list(
        await db.scalars(
            select(User.id).where(func.lower(User.email) == email.lower()).limit(2)
        )
    )


def member_ids(school_id: int | None, organisation_id: UUID | None):
    if school_id is None:
        return select(OrganisationMembership.user_id).where(
            OrganisationMembership.organisation_id == organisation_id
        )
    return (
        select(Educator.id)
        .where(Educator.school_id == school_id)
        .union(
            select(LibraryMembership.user_id).where(
                LibraryMembership.school_id == school_id
            ),
            select(OrganisationMembership.user_id).where(
                OrganisationMembership.organisation_id == organisation_id
            ),
        )
    )


async def page(
    db: AsyncSession, school_id, organisation_id, q: str, skip: int, limit: int
):
    query = select(User.id).where(User.id.in_(member_ids(school_id, organisation_id)))
    if q:
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.where(
            or_(
                User.name.ilike(f"%{escaped}%", escape="\\"),
                User.email.ilike(f"%{escaped}%", escape="\\"),
            )
        )
    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    ids = list(
        await db.scalars(
            query.order_by(func.lower(User.name), User.id).offset(skip).limit(limit)
        )
    )
    rows = (
        (
            await db.execute(
                select(
                    User.id,
                    User.name,
                    User.email,
                    User.is_active,
                    User.last_login_at,
                    User.type,
                    Educator.__table__.c.school_id,
                )
                .outerjoin(Educator.__table__, Educator.__table__.c.id == User.id)
                .where(User.id.in_(ids))
            )
        )
        .mappings()
        .all()
    )
    direct = (
        dict(
            (
                await db.execute(
                    select(LibraryMembership.user_id, LibraryMembership.role).where(
                        LibraryMembership.school_id == school_id,
                        LibraryMembership.user_id.in_(ids),
                    )
                )
            ).all()
        )
        if school_id is not None
        else {}
    )
    inherited = set(
        await db.scalars(
            select(OrganisationMembership.user_id).where(
                OrganisationMembership.organisation_id == organisation_id,
                OrganisationMembership.user_id.in_(ids),
            )
        )
    )
    by_id = {row["id"]: row for row in rows}
    return [by_id[user_id] for user_id in ids], total, direct, inherited


async def set_home(db: AsyncSession, user_id: UUID, school_id: int | None, role: str):
    if role == "school_admin":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        await db.execute(
            pg_insert(SchoolAdmin.__table__)
            .values(id=user_id, school_admin_info={})
            .on_conflict_do_nothing()
        )
    else:
        await db.execute(
            delete(SchoolAdmin.__table__).where(SchoolAdmin.__table__.c.id == user_id)
        )
    await db.execute(
        update(Educator.__table__)
        .where(Educator.__table__.c.id == user_id)
        .values(school_id=school_id)
    )
    await db.execute(
        update(User.__table__)
        .where(User.id == user_id)
        .values(type=UserAccountType(role))
    )


async def active_home_admin_count(db: AsyncSession, school_id: int):
    return await db.scalar(
        select(func.count())
        .select_from(SchoolAdmin)
        .where(Educator.school_id == school_id, User.is_active.is_(True))
    )


async def create_person(db: AsyncSession, name: str, email: str) -> UUID:
    user_id = await db.scalar(select(func.uuidv7()))
    await db.execute(
        insert(User.__table__).values(
            id=user_id,
            name=name,
            email=email.lower(),
            type=UserAccountType.EDUCATOR,
            is_active=True,
        )
    )
    await db.execute(insert(Educator.__table__).values(id=user_id, school_id=None))
    return user_id


async def lock_email(db: AsyncSession, email: str):
    await db.execute(
        select(func.pg_advisory_xact_lock(func.hashtextextended(email.lower(), 0)))
    )


async def recently_emailed(db: AsyncSession, user_id: UUID):
    return (
        await db.scalar(
            select(EventOutbox.id)
            .where(
                EventOutbox.user_id == user_id,
                EventOutbox.destination.like("email:%"),
                EventOutbox.created_at > datetime.utcnow() - timedelta(minutes=5),
            )
            .limit(1)
        )
        is not None
    )

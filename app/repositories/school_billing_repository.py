from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.school_billing import (
    OPEN_COLLECTIBLE_ATTEMPT_STATUSES,
    SchoolBillingAccount,
    SchoolBillingAttempt,
)


class SchoolBillingRepository:
    async def get_attempt(
        self, session: AsyncSession, attempt_id: UUID
    ) -> SchoolBillingAttempt | None:
        return await session.get(SchoolBillingAttempt, attempt_id)

    async def get_attempt_by_client_key(
        self, session: AsyncSession, school_id: UUID, client_key: str
    ) -> SchoolBillingAttempt | None:
        return (
            await session.execute(
                select(SchoolBillingAttempt).where(
                    SchoolBillingAttempt.school_id == school_id,
                    SchoolBillingAttempt.client_idempotency_key == client_key,
                )
            )
        ).scalar_one_or_none()

    async def get_open_attempt(
        self, session: AsyncSession, school_id: UUID
    ) -> SchoolBillingAttempt | None:
        return (
            await session.execute(
                select(SchoolBillingAttempt)
                .where(
                    SchoolBillingAttempt.school_id == school_id,
                    SchoolBillingAttempt.status.in_(OPEN_COLLECTIBLE_ATTEMPT_STATUSES),
                )
                .order_by(SchoolBillingAttempt.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def get_account(
        self, session: AsyncSession, school_id: UUID
    ) -> SchoolBillingAccount | None:
        return await session.get(SchoolBillingAccount, school_id)

    async def add_attempt(
        self, session: AsyncSession, attempt: SchoolBillingAttempt
    ) -> None:
        session.add(attempt)
        await session.flush()

    async def add_account(
        self, session: AsyncSession, account: SchoolBillingAccount
    ) -> None:
        session.add(account)
        await session.flush()


school_billing_repository = SchoolBillingRepository()

from typing import Literal
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.service_account import ServiceAccount
from app.models.user import User

AccountRefType = Literal["user", "service_account"]


def get_account_ref(
    account: User | ServiceAccount | None,
) -> tuple[AccountRefType | None, UUID | None]:
    if isinstance(account, User):
        return "user", account.id
    if isinstance(account, ServiceAccount):
        return "service_account", account.id
    return None, None


def load_account_ref(
    session: Session, account_type: AccountRefType | None, account_id: UUID | None
) -> User | ServiceAccount | None:
    if account_type is None or account_id is None:
        return None

    if account_type == "user":
        return session.get(User, account_id)
    if account_type == "service_account":
        return session.get(ServiceAccount, account_id)

    return None

from types import SimpleNamespace
from unittest.mock import Mock

from app.models.user import UserAccountType
from app.schemas.users.user_update import UserUpdateIn
from app.services.account_access import validate_account_access_change


def test_student_updates_keep_existing_authorization():
    session = Mock()
    validate_account_access_change(
        session,
        SimpleNamespace(type=UserAccountType.STUDENT),
        UserUpdateIn(is_active=False),
        is_staff=False,
    )
    session.scalar.assert_not_called()

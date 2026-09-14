from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.api.auth import secure_user_endpoint


@pytest.mark.parametrize(
    "claims", [{}, {"email_verified": False}, {"email_verified": "true"}]
)
def test_invited_account_requires_verified_email(claims):
    session = MagicMock()
    with pytest.raises(HTTPException) as error:
        secure_user_endpoint(
            firebase_user=MagicMock(), raw_data=claims, session=session
        )
    assert error.value.status_code == 401
    session.execute.assert_not_called()

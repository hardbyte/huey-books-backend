import base64
import hashlib
import hmac
import secrets
from datetime import timedelta
from typing import Optional, Union

from fastapi import Depends, Header, HTTPException, Request
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer,
    OAuth2PasswordBearer,
)
from fastapi_permissions import Authenticated, Everyone
from jose import jwt
from pydantic import ValidationError
from sqlalchemy.orm import Session
from starlette import status
from structlog import get_logger

from app import crud
from app.config import get_settings
from app.db.session import get_session
from app.models import ServiceAccount, ServiceAccountType, User
from app.models.user import UserAccountType
from app.repositories.service_account_repository import service_account_repository
from app.services.security import (
    TokenPayload,
    create_access_token,
    get_payload_from_access_token,
)

settings = get_settings()
logger = get_logger()

auth_scheme = OAuth2PasswordBearer(tokenUrl="/auth/access-token")

credentials_exception = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_auth_header_data(
    http_auth: HTTPAuthorizationCredentials = Depends(HTTPBearer(auto_error=False)),
) -> str:
    if http_auth is None:
        raise credentials_exception

    token = http_auth.credentials

    if not token:
        raise credentials_exception

    return token


async def get_valid_token_data(
    token: str = Depends(get_auth_header_data),
) -> TokenPayload:
    # logger.debug("Headers contain an Authorization component")
    try:
        return get_payload_from_access_token(token)
    except (jwt.JWTError, ValidationError) as e:
        logger.warning("Invalid access token")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Could not validate credentials",
        ) from e


def get_optional_auth_header_data(
    http_auth: HTTPAuthorizationCredentials = Depends(HTTPBearer(auto_error=False)),
) -> Optional[str]:
    """Get optional authentication token without raising exceptions."""
    if http_auth is None:
        return None
    return http_auth.credentials if http_auth.credentials else None


async def get_optional_token_data(
    token: Optional[str] = Depends(get_optional_auth_header_data),
) -> Optional[TokenPayload]:
    """Get optional token data without raising exceptions."""
    if token is None:
        return None

    try:
        return get_payload_from_access_token(token)
    except (jwt.JWTError, ValidationError):
        logger.debug("Invalid or missing access token")
        return None


def get_user_from_valid_token(
    db: Session = Depends(get_session),
    token_data: TokenPayload = Depends(get_valid_token_data),
) -> Optional[User]:
    """Get user from valid token if token is for user account, None if service account.

    Note: This function REQUIRES a valid token - use get_optional_authenticated_user
    for truly optional authentication scenarios.
    """
    # The subject of the JWT is either a user identifier or service account identifier
    # "wriveted:service-account:XXX" or "wriveted:user-account:XXX"
    aud, access_token_type, identifier = token_data.sub.lower().split(":")

    if access_token_type == "user-account":
        user = crud.user.get(db, id=identifier)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
            )
        return user

    return None


def get_optional_service_account(
    db: Session = Depends(get_session),
    token_data: TokenPayload = Depends(get_valid_token_data),
) -> Optional[ServiceAccount]:
    """Get service account from valid token if token is for service account, None if user."""
    # The subject of the JWT is either a user identifier or service account identifier
    # "wriveted:service-account:XXX" or "wriveted:user-account:XXX"
    aud, access_token_type, identifier = token_data.sub.lower().split(":")

    if access_token_type == "service-account":
        return service_account_repository.get_or_404(db, id=identifier)

    return None


def get_optional_authenticated_user(
    db: Session = Depends(get_session),
    token_data: Optional[TokenPayload] = Depends(get_optional_token_data),
) -> Optional[User]:
    """Get user from token if present and valid, otherwise return None. Truly optional authentication.

    This allows anonymous access when no token is provided, unlike get_user_from_valid_token
    which requires a valid token.
    """
    if token_data is None:
        return None

    # The subject of the JWT is either a user identifier or service account identifier
    # "wriveted:service-account:XXX" or "wriveted:user-account:XXX"
    try:
        aud, access_token_type, identifier = token_data.sub.lower().split(":")
    except ValueError:
        logger.debug("Invalid token subject format")
        return None

    if access_token_type == "user-account":
        user = crud.user.get(db, id=identifier)
        if not user:
            logger.debug("User not found for token")
            return None
        return user

    return None


async def get_current_user(
    current_user: Optional[User] = Depends(get_user_from_valid_token),
) -> User:
    if current_user is None:
        raise HTTPException(status_code=403, detail="API requires a user")
    return current_user


async def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    if not current_user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user


def get_current_active_user_or_service_account(
    maybe_user: Optional[User] = Depends(get_user_from_valid_token),
    maybe_service_account: Optional[ServiceAccount] = Depends(
        get_optional_service_account
    ),
) -> Union[User, ServiceAccount]:
    # We need either a valid user or service account given the auth token
    if maybe_user is not None and maybe_user.is_active:
        return maybe_user
    elif maybe_service_account is not None and maybe_service_account.is_active:
        return maybe_service_account
    else:
        raise HTTPException(status_code=400, detail="Inactive account")


async def get_current_active_superuser_or_backend_service_account(
    user_or_service_account: Union[User, ServiceAccount] = Depends(
        get_current_active_user_or_service_account
    ),
) -> Union[User, ServiceAccount]:
    if isinstance(user_or_service_account, User):
        if not user_or_service_account.type == UserAccountType.WRIVETED:
            raise HTTPException(status_code=403, detail="Insufficient privileges")
    elif isinstance(user_or_service_account, ServiceAccount):
        if not user_or_service_account.type == ServiceAccountType.BACKEND:
            raise HTTPException(status_code=403, detail="Insufficient privileges")
    return user_or_service_account


async def get_current_active_superuser(
    current_user: User = Depends(get_current_active_user),
) -> User:
    """
    Require administrator access
    """
    if not current_user.type == UserAccountType.WRIVETED:
        raise HTTPException(status_code=403, detail="Insufficient privileges")
    return current_user


async def get_active_principals(
    maybe_user: Optional[User] = Depends(get_user_from_valid_token),
    maybe_service_account: Optional[ServiceAccount] = Depends(
        get_optional_service_account
    ),
    db: Session = Depends(get_session),
):
    """
    RBAC Access Control using https://github.com/holgi/fastapi-permissions

    Principals:
    - role:admin
    - role:educator
    - role:schooladmin
    - role:student
    - role:kiosk
    - role:lms
    - role:reader
    - role:parent

    - user:{id}
    - student:{school-id}
    - educator:{school-id}
    - schooladmin:{school-id}
    - parent:{reader-id}
    - child:{parent-id}
    - supporter:{reader-id}

    - Authenticated
    - Everyone

    Future Principals:
    - member:{group-id}

    Permissions:
    - CRUD (create, read, update, delete)

    Future permissions?
    - batch
    - share
    """

    # OAuth (MCP) tokens never reach here: they are rejected on the REST pipeline
    # (see get_raw_payload_from_access_token) and confined in-process by app/mcp.

    principals = [Everyone]

    if maybe_user is not None and maybe_user.is_active:
        user = maybe_user
        principals.append(Authenticated)
        # since the user type being returned from crud is dynamic based on type,
        # we can call the get_principals method on the user object to get a cascading
        # list of principals.
        # i.e. a student will have calculated principals of a user, a reader, and a student
        principals.extend(await user.get_principals())

    elif maybe_service_account is not None and maybe_service_account.is_active:
        service_account = maybe_service_account
        principals.append(Authenticated)

        if service_account.type == ServiceAccountType.BACKEND:
            principals.append("role:admin")
        elif service_account.type == ServiceAccountType.LMS:
            principals.append("role:lms")
        elif service_account.type == ServiceAccountType.SCHOOL:
            principals.append("role:school")
            principals.append("role:library")
        elif service_account.type == ServiceAccountType.KIOSK:
            principals.append("role:kiosk")

        # Service accounts can optionally be associated with multiple schools:
        # for school in service_account.schools:
        #     principals.append(f"school:{school.id}")

    return principals


def create_user_access_token(user, expires_delta=None):
    if expires_delta is None:
        delta = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    else:
        delta = expires_delta
    wriveted_access_token = create_access_token(
        subject=f"Wriveted:User-Account:{user.id}",
        # extra_claims={},
        expires_delta=delta,
    )
    logger.debug("Access token generated for user", user=user)
    return wriveted_access_token


async def verify_shopify_hmac(
    request: Request, x_shopify_hmac_sha256: str | None = Header(default=None)
):
    body = await request.body()
    digest = hmac.new(
        settings.SHOPIFY_HMAC_SECRET.encode("utf-8"),
        body,
        hashlib.sha256,
    ).digest()
    computed_hmac = base64.b64encode(digest)
    valid = secrets.compare_digest(computed_hmac, x_shopify_hmac_sha256.encode("utf-8"))
    if not valid:
        raise HTTPException(status_code=403, detail="Invalid SHA-256 HMAC")

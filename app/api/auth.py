from datetime import datetime
from typing import Literal, Union, cast
from uuid import UUID

import requests
import requests.exceptions
from fastapi import APIRouter, Depends, HTTPException
from fastapi_cloudauth.firebase import FirebaseClaims, FirebaseCurrentUser
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette import status
from structlog import get_logger
from tenacity import retry, stop_after_delay, wait_fixed

from app import crud
from app.api.dependencies.security import (
    create_user_access_token,
    get_current_active_user_or_service_account,
    get_valid_token_data,
)
from app.config import get_settings
from app.db.session import get_session
from app.models import EventLevel, Parent, SchoolState, ServiceAccount, Student, User
from app.models.user import UserAccountType
from app.repositories.class_group_repository import class_group_repository
from app.repositories.event_repository import event_repository
from app.repositories.school_repository import school_repository
from app.schemas.auth import AccountType, AuthenticatedAccountBrief
from app.schemas.users.educator import EducatorDetail
from app.schemas.users.parent import ParentDetail
from app.schemas.users.reader import PublicReaderDetail
from app.schemas.users.school_admin import SchoolAdminDetail
from app.schemas.users.student import StudentDetail, StudentIdentity
from app.schemas.users.user import UserDetail, UserInfo
from app.schemas.users.user_create import UserCreateIn
from app.schemas.users.wriveted_admin import WrivetedAdminDetail
from app.services.security import TokenPayload
from app.services.users import (
    link_parent_with_subscription_via_checkout_session,
    new_identifiable_username,
)

logger = get_logger()
config = get_settings()

router = APIRouter(tags=["Security"])


@retry(
    stop=stop_after_delay(180),  # Stop retrying after 180 seconds (3 minutes)
    wait=wait_fixed(10),  # Wait between retries
)
def get_firebase_user():
    return FirebaseCurrentUser(project_id=config.FIREBASE_PROJECT_ID)


get_current_firebase_user = None

try:
    get_current_firebase_user = get_firebase_user()
except requests.exceptions.ConnectionError:
    logger.warning(
        "Couldn't connect to Firebase SSO even after retries. Check that the server has access to the internet."
    )
    auth_firebase_user = None


get_raw_info = get_current_firebase_user.claim(None)


class Token(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"


@router.get(
    "/auth/firebase",
    responses={
        401: {"description": "Unauthorized"},
        422: {"description": "Invalid data"},
    },
    response_model=Token,
)
def secure_user_endpoint(
    firebase_user: FirebaseClaims = Depends(get_current_firebase_user),
    raw_data=Depends(get_raw_info),
    session: Session = Depends(get_session),
    create: bool = True,
    checkout_session_id: str | None = None,
):
    """Login to Wriveted API by exchanging a valid Firebase token.

    This API is used to create access tokens for users that have logged into a Wriveted
    controlled Firebase application - usually with a federated Google account.

    The generated access token is a JSON Web Token (JWT) which contains a user specific unique
    identifier so Wriveted can recognize the user when that access token is provided as part of
    an API call.

    Note: this API creates a new user by default. If opting out of creation, will throw a 401 if user is nonexisting.

    Updates existing users with the latest SSO data.
    (e.g. their profile picture).

    If a checkout_session_id is provided, the user will be associated with the subscription associated
    with that checkout session (provided everything checks out). Pun intended.
    """

    # If we have gotten this far the user has a valid firebase token
    logger.debug("Auth with firebase endpoint called", firebase_user=firebase_user)
    assert raw_data["email_verified"], "Firebase hasn't checked the email address"
    # Note firebase credentials may not include the users name

    email = firebase_user.email
    picture = raw_data.get("picture")
    name = raw_data.get("name", firebase_user.email)

    was_created = False

    if create:
        user_data = UserCreateIn(
            name=name,
            email=email,
            # NOW ADD THE USER_DATA STUFF
            info=UserInfo(
                sign_in_provider=raw_data["firebase"].get("sign_in_provider"),
                picture=picture,
            ),
        )
        user, was_created = crud.user.get_or_create(session, user_data)
    else:
        user = crud.user.get_by_account_email(session, email)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="No account"
            )

    if was_created:
        provider = raw_data.get("firebase", {}).get("sign_in_provider") or "unknown"
        event_repository.create(
            session=session,
            title="User account created",
            description=(
                f"{user.name or user.email or 'A user'} created an account "
                f"({user.type.value} via {provider})"
            ),
            account=user,
            commit=False,
        )
    else:
        event_repository.create(
            session=session,
            title="User logged in",
            description="",
            account=user,
            level=EventLevel.DEBUG,
            commit=False,
        )
    logger.info("Request to login from user", user=user)

    if not user.is_active:
        raise HTTPException(status_code=401, detail="Inactive user")

    # Note this replaces the user's info with the SSO data including their name and info.
    # crud.user.update(db=session, db_obj=user, obj_in=user_data)
    # Instead we only update the fields we want
    if user.info is None:
        user.info = {}
    user.info["picture"] = picture
    user.info["sign_in_provider"] = raw_data["firebase"].get("sign_in_provider")

    user.last_login_at = datetime.utcnow()
    session.add(user)
    session.commit()
    session.refresh(user)  # Refresh the user object after commit

    wriveted_access_token = create_user_access_token(user)

    if user.type == UserAccountType.PARENT and checkout_session_id:
        link_parent_with_subscription_via_checkout_session(
            session, cast(Parent, user), checkout_session_id
        )

    return {
        "access_token": wriveted_access_token,
        "token_type": "bearer",
    }


class ClassCodeUserLogIn(BaseModel):
    username: str
    class_joining_code: str


@router.post(
    "/auth/class-code",
    response_model=Token,
    responses={
        401: {"description": "Unauthorized"},
        422: {"description": "Invalid data"},
    },
)
def student_user_auth(
    data: ClassCodeUserLogIn,
    session: Session = Depends(get_session),
):
    """Login to Wriveted API as a student by posting a valid username and class code.

    This API is used to create access tokens for existing student users.

    The generated access token is a JSON Web Token (JWT) which contains a user specific unique
    identifier so Wriveted can recognize the user when that access token is provided as part of
    an API call.

    Note this API doesn't create new users.
    """
    logger.debug("Processing student login request")

    # Get the class by joining code or 401
    class_group = class_group_repository.get_by_class_code(
        session, data.class_joining_code
    )
    if class_group is None:
        raise HTTPException(status_code=401, detail="Unauthorized")

    logger.debug(
        "Get the school associated with the given class group",
        school=class_group.school,
    )
    school = class_group.school
    if not school:
        raise HTTPException(status_code=401, detail="Invalid class group")

    logger.debug("Get the user by username")
    # if the school doesn't match -> 401
    user = crud.user.get_student_by_username_and_school_id(
        session, username=data.username, school_id=school.id
    )
    # Note the user could have been moved to another class, or removed from a class but we log them in anyway
    if (
        user is None
        or user.school is not school
        or user.type not in {UserAccountType.STUDENT, UserAccountType.PUBLIC}
    ):
        raise HTTPException(status_code=401, detail="Unauthorized")

    logger.debug("Check active user and school")
    # Check the school + user is active else 403 (difference being the server knows who you are)
    if not user.is_active or school.state != SchoolState.ACTIVE:
        logger.info("User active?", r=user.is_active)
        logger.info("School active", school_state=school.state)
        logger.warning(
            "Login attempt to inactive user or school", user=user, school=school
        )
        raise HTTPException(status_code=401, detail="Unauthorized")

    event_repository.create(
        session=session,
        title="User logged in",
        description="Student logged in",
        account=user,
        level=EventLevel.DEBUG,
        commit=False,
    )

    user.last_login_at = datetime.utcnow()
    session.add(user)
    session.commit()
    logger.debug("Generating access token")

    wriveted_access_token = create_user_access_token(user)

    return {
        "access_token": wriveted_access_token,
        "token_type": "bearer",
    }


class RegisterUserIn(BaseModel):
    first_name: str | None = None
    last_name_initial: str | None = None
    school_id: UUID
    class_joining_code: str


@router.post("/auth/register-student", response_model=StudentIdentity)
def create_student_user(
    data: RegisterUserIn,
    session: Session = Depends(get_session),
):
    """Create a new student account associated with a school by posting a valid class code.

    Note this API always creates a new user, to log in to an existing account see `/auth/class-code`
    """

    school = school_repository.get_by_wriveted_id_or_404(
        db=session, wriveted_id=str(data.school_id)
    )

    # Check the class joining code belongs to this school
    class_group = class_group_repository.get_by_class_code(
        db=session, code=data.class_joining_code
    )
    if class_group.school_id != data.school_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Access is unauthorized"
        )

    # Note this generates a valid username based on the name
    username = new_identifiable_username(
        data.first_name, data.last_name_initial, session, school.id
    )
    new_user = Student(
        type=UserAccountType.STUDENT,
        is_active=True,
        name=f"{data.first_name} {data.last_name_initial}",
        username=username,
        first_name=data.first_name,
        last_name_initial=data.last_name_initial,
        school_id=school.id,
        class_group_id=class_group.id,
        info={"sign_in_provider": "class-code"},
    )
    session.add(new_user)

    event_repository.create(
        session=session,
        title="Student account created",
        description=f"User type: {new_user.type}",
        account=new_user,
        school=school,
        commit=False,
    )
    session.commit()
    session.refresh(new_user)

    return StudentIdentity.model_validate(new_user)


@router.get("/auth/me", response_model=AuthenticatedAccountBrief)
def get_current_user(
    token_data: TokenPayload = Depends(get_valid_token_data),
    current_user_or_service_account: Union[User, ServiceAccount] = Depends(
        get_current_active_user_or_service_account
    ),
):
    """
    Test that the presented credentials are valid, returning details on the logged in user or service account.
    """
    logger.debug("Testing user token", account=current_user_or_service_account)
    if isinstance(current_user_or_service_account, User):
        logger.info("User type", user_type=current_user_or_service_account.type)
        match current_user_or_service_account.type:
            case UserAccountType.STUDENT:
                user_detail = StudentDetail.model_validate(
                    current_user_or_service_account
                )
            case UserAccountType.WRIVETED:
                user_detail = WrivetedAdminDetail.model_validate(
                    current_user_or_service_account
                )
            case UserAccountType.EDUCATOR:
                user_detail = EducatorDetail.model_validate(
                    current_user_or_service_account
                )
            case UserAccountType.SCHOOL_ADMIN:
                user_detail = SchoolAdminDetail.model_validate(
                    current_user_or_service_account
                )
            case UserAccountType.PUBLIC:
                user_detail = PublicReaderDetail.model_validate(
                    current_user_or_service_account
                )
            case UserAccountType.PARENT:
                user_detail = ParentDetail.model_validate(
                    current_user_or_service_account
                )
            case _:
                logger.info("Generic user")
                user_detail = UserDetail.model_validate(current_user_or_service_account)

        return AuthenticatedAccountBrief(
            account_type=AccountType.user,
            user=user_detail,
            token_expiry=token_data.exp,
        )

    elif isinstance(current_user_or_service_account, ServiceAccount):
        return AuthenticatedAccountBrief(
            account_type=AccountType.service_account,
            service_account=current_user_or_service_account,
            token_expiry=token_data.exp,
        )
    else:
        raise NotImplementedError("Hmm")


class E2ETestAuthRequest(BaseModel):
    """Request for E2E test authentication."""

    secret: str
    email: str
    user_type: UserAccountType = UserAccountType.WRIVETED
    school_id: int | None = None


@router.post(
    "/auth/e2e-test",
    response_model=Token,
    responses={
        401: {"description": "E2E test auth not enabled or invalid secret"},
        422: {"description": "Invalid data"},
    },
    include_in_schema=False,
)
def e2e_test_auth(
    request: E2ETestAuthRequest,
    session: Session = Depends(get_session),
):
    """
    Generate an auth token for E2E testing without Firebase authentication.

    This endpoint is only available when E2E_TEST_AUTH_SECRET is configured.
    It creates or retrieves a test user and returns a valid JWT token.

    SECURITY: This endpoint should NEVER be enabled in production.
    """
    if not config.E2E_TEST_AUTH_SECRET:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="E2E test authentication is not enabled",
        )

    if request.secret != config.E2E_TEST_AUTH_SECRET:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid E2E test auth secret",
        )

    logger.warning(
        "E2E test auth endpoint used - should only be enabled in test environments",
        email=request.email,
    )

    # Handle school-based user types (EDUCATOR, SCHOOL_ADMIN)
    school_id = request.school_id
    if request.user_type in (UserAccountType.EDUCATOR, UserAccountType.SCHOOL_ADMIN):
        if school_id is None:
            # Get or create a test school for E2E testing
            from sqlalchemy import select

            from app.models import School

            test_school = session.execute(
                select(School).where(School.name == "E2E Test School")
            ).scalar_one_or_none()
            if test_school is None:
                test_school = School(
                    name="E2E Test School",
                    country_code="NZL",
                    state=SchoolState.ACTIVE,
                    info={
                        "location": {
                            "suburb": "Test",
                            "state": "Test",
                            "postcode": "0000",
                        }
                    },
                )
                session.add(test_school)
                session.commit()
                session.refresh(test_school)
                logger.info("Created E2E test school", school_id=test_school.id)
            school_id = test_school.id

    user_data = UserCreateIn(
        name=f"E2E Test User ({request.email})",
        email=request.email,
        type=request.user_type,
        school_id=school_id,
        info=UserInfo(
            sign_in_provider="e2e_test",
            picture=None,
        ),
    )
    user, was_created = crud.user.get_or_create(session, user_data)

    if was_created:
        logger.info("Created E2E test user", user_id=user.id, email=request.email)

    if not user.is_active:
        user.is_active = True
        session.add(user)
        session.commit()

    wriveted_access_token = create_user_access_token(user)

    return {
        "access_token": wriveted_access_token,
        "token_type": "bearer",
    }

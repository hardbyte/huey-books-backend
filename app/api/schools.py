import copy
from typing import Any, List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query, Security
from fastapi_permissions import Allow, Authenticated, Deny, has_permission
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette import status
from structlog import get_logger

from app.api.common.pagination import PaginatedQueryParams
from app.api.dependencies.async_db_dep import DBSessionDep
from app.api.dependencies.school import (
    aget_school_from_wriveted_id,
    get_school_from_wriveted_id,
)
from app.api.dependencies.security import (
    get_active_principals,
    get_current_active_user_or_service_account,
    get_current_user,
)
from app.db.session import get_session
from app.models import School, SchoolAdmin, ServiceAccount
from app.models.user import User
from app.permissions import Permission
from app.repositories.event_repository import event_repository
from app.repositories.school_repository import school_repository
from app.schemas.school import (
    SchoolBookbotInfo,
    SchoolCreateIn,
    SchoolDetail,
    SchoolPatchOptions,
    SchoolSelectorOption,
)
from app.services.experiments import get_experiments
from app.utils.dict_utils import deep_merge_dicts

logger = get_logger()

router = APIRouter(
    tags=["Schools"],
    dependencies=[Security(get_current_active_user_or_service_account)],
)

public_router = APIRouter(tags=["Public", "Schools"])

bulk_school_access_control_list = [
    (Allow, Authenticated, "read"),
    # if a user finds their school in the list,
    # proceeding with onboarding will "bind" their account
    # to the selected school, and mark its status as pending
    (Allow, Authenticated, "bind"),
    (Allow, Authenticated, "update"),
    # if a user can't find their school in the list,
    # we need to create a school with their provided details
    (Allow, Authenticated, "create"),
    (Allow, "role:admin", "create"),
    (Allow, "role:admin", "read-collection"),
    (Allow, "role:admin", "batch"),
    (Allow, "role:admin", "details"),
    (Allow, "role:library", "details"),
    (Allow, "role:lms", "details"),
    (Allow, "role:lms", "read"),
    (Allow, "role:lms", "read-collection"),
    # The following explicitly blocks LMS accounts from creating new schools
    (Deny, "role:lms", "create"),
    (Deny, "role:lms", "batch"),
]


def _get_huey_terms_acceptance(info: Any) -> Optional[dict]:
    if not isinstance(info, dict):
        return None
    terms_acceptance = info.get("terms_acceptance")
    if not isinstance(terms_acceptance, dict):
        return None
    huey_terms = terms_acceptance.get("huey_books")
    return huey_terms if isinstance(huey_terms, dict) else None


def _is_terms_only_info_patch(patch_info: Any) -> bool:
    if not isinstance(patch_info, dict):
        return False
    if set(patch_info.keys()) != {"terms_acceptance"}:
        return False
    terms_acceptance = patch_info.get("terms_acceptance")
    if not isinstance(terms_acceptance, dict):
        return False
    if set(terms_acceptance.keys()) != {"huey_books"}:
        return False
    return isinstance(terms_acceptance.get("huey_books"), dict)


def _is_terms_only_patch(patch: SchoolPatchOptions, patch_info: Any) -> bool:
    if any(
        [
            patch.status,
            patch.bookbot_type,
            patch.lms_type,
            patch.name,
            patch.student_domain,
            patch.teacher_domain,
        ]
    ):
        return False
    return _is_terms_only_info_patch(patch_info)


@router.get(
    "/schools",
    response_model=List[SchoolSelectorOption],
    dependencies=[
        # allow batch schools operations for any Authenticated user
        # that has the "read" permission - added in the school ACL.
        # Note we also filter the schools given the principals' permission
        # to ensure only schools that the account is allowed to see are
        # returned. Ref: https://github.com/holgi/fastapi-permissions/issues/3
        Permission("read", bulk_school_access_control_list)
    ],
)
async def get_schools(
    session: DBSessionDep,
    country_code: Optional[str] = Query(None, description="Filter schools by country"),
    state: Optional[str] = Query(None, description="Filter schools by state"),
    postcode: Optional[str] = Query(None, description="Filter schools by postcode"),
    q: Optional[str] = Query(None, description="Filter schools by name"),
    is_active: Optional[bool] = Query(
        None, description="Return active or inactive schools. Default is all."
    ),
    has_active_subscription: Optional[bool] = Query(
        None, description="Return schools with active subscription. Default is all."
    ),
    connected_collection: Optional[bool] = Query(
        None,
        description="Return schools that have connected their collection. Default is all.",
    ),
    official_identifier: Optional[str] = Query(
        None,
        description="Official identifier for the school - Country specific and usually government issued",
    ),
    pagination: PaginatedQueryParams = Depends(),
    principals: List = Depends(get_active_principals),
):
    """
    List of schools showing only publicly available information.
    Available to any valid user, primarily for selection upon signup.

    Provide any of country code, state/region, postcode, and/or school name query to further filter the schools.

    Admins can also optionally filter by:
    - active/inactive schools using the `is_active`
    - collection uploaded using `connected_collection`
    """
    has_details_permission = has_permission(
        principals, "details", bulk_school_access_control_list
    )
    has_collection_permission = has_permission(
        principals, "read-collection", bulk_school_access_control_list
    )

    schools = await school_repository.get_all_with_optional_filters(
        session,
        country_code=country_code,
        state=state,
        postcode=postcode,
        query_string=q,
        is_active=is_active if has_details_permission else None,
        is_collection_connected=(
            connected_collection if has_details_permission else None
        ),
        has_active_subscription=has_active_subscription,
        official_identifier=official_identifier,
        skip=pagination.skip,
        limit=pagination.limit,
    )

    logger.debug(f"Returning {len(schools)} schools")

    # Sanitize results based on logged-in user's permissions
    if not has_details_permission:
        for school in schools:
            school.state = None
            school.active_subscription = None

    if not has_collection_permission:
        for school in schools:
            school.collection = None

    # Load the subscription data with

    return schools


@router.get("/school/{wriveted_identifier}", response_model=SchoolDetail)
async def get_school(
    school: School = Permission("read", get_school_from_wriveted_id),
):
    """
    Detail on a particular school

    🔒 Requires "read" permission on the school.
    """
    return school


@public_router.get(
    "/school/{wriveted_identifier}/exists",
    dependencies=[Depends(get_school_from_wriveted_id)],
)
async def school_exists():
    """
    Whether a school exists. Used for the publicly-accessible Bookbot chat links.
    """
    # dependency will automatically 404 if school doesn't exist
    return True


@public_router.get("/school/{wriveted_identifier}/bot")
async def get_school_bookbot_type(
    school: School = Depends(get_school_from_wriveted_id),
):
    """
    Returns the Huey-relevant information for a school, i.e. whether they've opted for Huey's Collection,
    or their own, and which "experiments" the school has activated, if any.
    Used for the publicly-accessible Bookbot chat links.
    """
    result = {
        "name": school.name,
        "type": school.bookbot_type,
        "experiments": (school.info or {}).get("experiments", {}),
        "supporter": school.subscription is not None and school.subscription.is_active,
    }

    if school.collection:
        result["collection_updated_at"] = school.collection.updated_at.isoformat()
        result["collection_book_count"] = school.collection.book_count
    else:
        result["collection_updated_at"] = None
        result["collection_book_count"] = 0

    return result


@router.get("/school/{wriveted_identifier}/bookbot", response_model=SchoolBookbotInfo)
async def get_school_bookbot_info(
    school: School = Permission("read", get_school_from_wriveted_id),
):
    """
    Retrieve bookbot related info on a particular school.
    """
    return school


@router.patch("/school/{wriveted_identifier}/admin")
async def bind_school(
    school: School = Permission("bind", get_school_from_wriveted_id),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """
    Binds the current user to a school as its administrator.
    Will fail if target school already has an admin.
    """
    if not isinstance(user, SchoolAdmin):
        raise HTTPException(
            status.HTTP_401_UNATHORIZED, "User not a school administrator."
        )

    if school.admin is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "School already bound to an admin user."
        )

    school.admin_id = user.id
    user.school_id = school.id
    session.commit()

    return school.admin_id


@router.patch("/school/{wriveted_identifier}", response_model=SchoolDetail)
async def update_school(
    session: DBSessionDep,
    patch: SchoolPatchOptions,
    school: School = Permission("update", aget_school_from_wriveted_id),
    account: Union[User, ServiceAccount] = Depends(
        get_current_active_user_or_service_account
    ),
):
    """
    Update a school.

    Only available to users with the "update" permission for the
    selected school; i.e. superusers, and school administrators.
    """
    patch_info = patch.info
    original_info = copy.deepcopy(school.info or {})
    merged_info = None
    patch_data = patch.model_copy(deep=True)

    if patch_info is not None:
        if isinstance(patch_info, dict):
            merged_info = copy.deepcopy(original_info)
            deep_merge_dicts(merged_info, patch_info)
        else:
            merged_info = patch_info
        patch_data.info = merged_info

    terms_changed = False
    terms_payload = None
    if merged_info is not None:
        terms_payload = _get_huey_terms_acceptance(merged_info)
        terms_changed = terms_payload != _get_huey_terms_acceptance(original_info)

    terms_only = _is_terms_only_patch(patch_data, patch_info)

    if patch_data.status:
        if patch_data.status != school.state:
            await event_repository.acreate(
                session=session,
                title=f"School account made {patch_data.status.upper()}",
                description=(
                    f"School '{school.name}' status updated to "
                    f"{patch_data.status.upper()}"
                ),
                school=school,
                account=account,
            )
        school.state = patch_data.status

    if terms_changed and terms_payload:
        accepted_by_user_id = terms_payload.get("accepted_by_user_id")
        if accepted_by_user_id is None and isinstance(account, User):
            accepted_by_user_id = str(account.id)
        await event_repository.acreate(
            session=session,
            title="Huey Books terms accepted",
            description=(
                f"School '{school.name}' accepted Huey Books terms "
                f"v{terms_payload.get('version')}"
            ),
            info={
                "terms_version": terms_payload.get("version"),
                "accepted_at": terms_payload.get("accepted_at"),
                "accepted_by_user_id": accepted_by_user_id,
            },
            school=school,
            account=account,
            commit=False,
        )

    if not terms_only:
        await event_repository.acreate(
            session=session,
            title="School Updated",
            description=f"School '{school.name}' in {school.country.name} updated.",
            school=school,
            account=account,
            commit=False,
        )
    updated_orm_object = await school_repository.aupdate(
        db=session, db_obj=school, obj_in=patch_data
    )

    return updated_orm_object


@router.post(
    "/schools",
    dependencies=[
        Permission("batch", bulk_school_access_control_list),
    ],
)
async def bulk_add_schools(
    schools: List[SchoolCreateIn],
    account: Union[User, ServiceAccount] = Depends(
        get_current_active_user_or_service_account
    ),
    session: Session = Depends(get_session),
):
    """Bulk API to add schools"""

    new_schools = [
        school_repository.create(db=session, obj_in=school_data, commit=False)
        for school_data in schools
    ]

    for school in new_schools:
        school.info["experiments"] = get_experiments(school=school)

    event_repository.create(
        session=session,
        title="Bulk created schools",
        description=f"Added {len(new_schools)} schools to database.",
        info={"identifiers": [s.wriveted_identifier for s in new_schools]},
        account=account,
        commit=False,
    )
    try:
        session.commit()
        return {"msg": f"Added {len(new_schools)} new schools"}
    except IntegrityError:
        logger.warning("there was an issue importing bulk school data")
        raise HTTPException(500, "Error bulk importing schools")


@router.post(
    "/school",
    dependencies=[
        Permission("create", bulk_school_access_control_list),
    ],
    response_model=SchoolDetail,
)
def add_school(
    school: SchoolCreateIn,
    account: Union[User, ServiceAccount] = Depends(
        get_current_active_user_or_service_account
    ),
    session: Session = Depends(get_session),
):
    try:
        school_orm = school_repository.create(db=session, obj_in=school, commit=False)
        school_orm.info["experiments"] = get_experiments(school=school_orm)
        session.commit()
        event_repository.create(
            session=session,
            title="New school created",
            description=f"{account.name} created school '{school.name}'",
            school=school_orm,
            account=account,
        )
        return school_orm
    except IntegrityError as e:
        logger.warning("Database integrity error while adding school", exc_info=e)
        raise HTTPException(
            status_code=422,
            detail="Couldn't add school to database. It might already exist? Check the country code.",
        )


@router.delete("/school/{wriveted_identifier}")
def delete_school(
    school: School = Permission("delete", get_school_from_wriveted_id),
    account=Depends(get_current_active_user_or_service_account),
    session: Session = Depends(get_session),
):
    logger.info("Deleting a school", account=account, school=school)
    event_repository.create(
        session=session,
        title="School Deleted",
        description=f"School {school.name} in {school.country.name} deleted.",
        account=account,
        commit=False,
    )
    school_repository.remove(db=session, obj_in=school)
    return {"msg": "School deleted"}

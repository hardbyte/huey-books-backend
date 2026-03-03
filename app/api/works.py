from typing import List, Optional

from fastapi import APIRouter, Depends, Path, Security
from fastapi.params import Query
from fastapi_permissions import All, Allow, Authenticated
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session
from structlog import get_logger

from app import crud
from app.api.common.pagination import PaginatedQueryParams
from app.api.dependencies.security import (
    get_current_active_superuser_or_backend_service_account,
    get_current_active_user_or_service_account,
)
from app.crud.base import compare_dicts
from app.db.session import get_session
from app.models import Author, Work
from app.models.edition import Edition
from app.models.work import WorkType
from app.permissions import Permission
from app.repositories.author_repository import author_repository
from app.repositories.edition_repository import edition_repository
from app.repositories.labelset_repository import labelset_repository
from app.repositories.work_repository import work_repository
from app.schemas.work import (
    WorkBrief,
    WorkCreateWithEditionsIn,
    WorkDetail,
    WorkEnriched,
    WorkUpdateIn,
)
from app.services.background_tasks import queue_background_task
from app.services.editions import get_definitive_isbn

"""
Access control rules applying to all Works endpoints.

No further access control is applied on a per Work basis.
"""
bulk_work_access_control_list = [
    (Allow, "role:admin", All),
    (Allow, "role:educator", All),
    (Allow, Authenticated, "read"),
]

router = APIRouter(
    tags=["Books"], dependencies=[Depends(get_current_active_user_or_service_account)]
)

logger = get_logger()


def get_work(
    work_id: int = Path(
        ...,
        description="Identifier for a unique creative work in the Wriveted database",
    ),
    session: Session = Depends(get_session),
) -> Work:
    return work_repository.get_or_404(db=session, id=work_id)


@router.get(
    "/works",
    response_model=List[WorkBrief],
)
def get_works(
    query: Optional[str] = Query(None, description="Query string"),
    author_id: Optional[int] = Query(None, description="Author's Wriveted Id"),
    isbn: Optional[str] = Query(None, description="Isbn"),
    type: Optional[WorkType] = Query(WorkType.BOOK),
    pagination: PaginatedQueryParams = Depends(),
    session: Session = Depends(get_session),
):
    works_query = work_repository.get_all_query(session).where(Work.type == type)

    if author_id is not None:
        works_query = works_query.where(Work.authors.any(Author.id == author_id))

    if query is not None:
        works_query = works_query.where(func.lower(Work.title).contains(query.lower()))

    if isbn is not None:
        works_query = works_query.where(Work.editions.any(Edition.isbn == isbn))
    else:
        # Ensure there is one or more editions...
        works_query = works_query.where(Work.editions.any())

    works = (
        session.execute(
            work_repository.apply_pagination(
                works_query, skip=pagination.skip, limit=pagination.limit
            )
        )
        .scalars()
        .all()
    )

    output = []
    for work in works:
        brief = {}
        brief["id"] = work.id
        brief["type"] = work.type
        brief["leading_article"] = work.leading_article
        brief["title"] = work.title
        brief["subtitle"] = work.subtitle
        brief["authors"] = [
            {
                "id": author.id,
                "first_name": author.first_name,
                "last_name": author.last_name,
            }
            for author in work.authors
        ]
        output.append(brief)

    return output


@router.get("/work/{work_id}", response_model=WorkDetail | WorkEnriched)
def get_work_by_id(
    work: Work = Depends(get_work),
    full_detail: bool = Query(
        default=True,
        title="Full recursive detail",
        description="If enabled, will include information for each of the work's editions.",
    ),
    session: Session = Depends(get_session),
):
    if full_detail:
        if work.info is not None and "genres" not in work.info:
            work.info["genres"] = []
            work.info["other"] = {}
            session.add(work)
            session.commit()
        return WorkDetail.model_validate(work)

    else:
        output = WorkEnriched.model_validate(work)
        first_edition_with_cover = session.scalar(
            select(Edition).where(
                and_(Edition.cover_url.isnot(None), Edition.work_id == work.id)
            )
        )
        output.cover_url = (
            first_edition_with_cover.cover_url if first_edition_with_cover else None
        )

        return output


@router.post(
    "/work/{work_id}/generate-labels",
    dependencies=[Security(get_current_active_superuser_or_backend_service_account)],
)
def generate_work_label(work: Work = Depends(get_work)):
    """
    Queue an internal task to generate a label for a work using GPT-4.

    This is an experimental, admin only endpoint.
    """
    queue_background_task(
        "generate-labels",
        {"work_id": work.id},
    )
    return {"status": "ok"}


@router.get(
    "/work/{work_id}/labelling-prompt",
    dependencies=[Security(get_current_active_superuser_or_backend_service_account)],
)
def get_labelling_prompt(work: Work = Depends(get_work)):
    """
    Return the assembled labelling prompt for a work, so users can
    copy it into their own LLM tools.
    """
    from app.services.labelling import prepare_context_for_labelling
    from app.services.labelling.prompt import system_prompt

    user_content = prepare_context_for_labelling(work)
    return {
        "system_prompt": system_prompt.strip(),
        "user_prompt": user_content.strip(),
    }


@router.post(
    "/work",
    response_model=WorkDetail,
    dependencies=[
        Permission("create", bulk_work_access_control_list),
    ],
)
def create_work_with_editions(
    work_data: WorkCreateWithEditionsIn,
    account=Depends(get_current_active_user_or_service_account),
    session: Session = Depends(get_session),
):
    """
    Create a new Work and optionally associate editions.

    Note if the editions already exist, they will be unlinked from any current Work.
    """
    logger.debug("Creating new work", data=work_data)
    edition_ids = work_data.editions
    del work_data.editions

    authors = [
        # Authors could be int or AuthorCreateIn objects
        (
            author_repository.get_by_id(db=session, author_id=author_data)
            if isinstance(author_data, int)
            else author_repository.get_or_create(db=session, author_data=author_data)
        )
        for author_data in work_data.authors
    ]
    logger.debug("Processed authors for new work", authors=authors)
    del work_data.authors
    work = work_repository.create(db=session, obj_in=work_data)
    work.authors = authors
    logger.debug("Created new work", work=work)

    for unsanitized_isbn in edition_ids:
        try:
            isbn = get_definitive_isbn(unsanitized_isbn)
        except AssertionError:
            logger.debug(
                f"Skipping edition with invalid ISBN: {unsanitized_isbn}", work=work
            )
            continue
        edition = edition_repository.get_or_create_unhydrated(db=session, isbn=isbn)
        edition.work = work
        session.add(edition)

    logger.debug(f"Associated {len(edition_ids)} editions with new work", work=work)
    crud.event.create(
        session,
        title="Work created",
        description=f"'{work.title}' created with {len(edition_ids)} editions",
        info={
            "work_id": work.id,
            "title": work.title,
        },
        account=account,
    )
    logger.debug("Added event and committed new work", work=work)
    return work


@router.patch(
    "/work/{work_id}",
    response_model=WorkDetail,
    dependencies=[
        Permission("update", bulk_work_access_control_list),
    ],
)
def update_work(
    changes: WorkUpdateIn,
    work_orm: Work = Depends(get_work),
    account=Depends(get_current_active_user_or_service_account),
    session: Session = Depends(get_session),
):
    logger.info("Updating work", target_work=work_orm, changes=changes)
    old_work_data = work_orm.get_dict(session)

    labelset_changes = {}
    if changes.labelset is not None:
        labelset_update = changes.labelset

        # if isinstance(account, User):
        #     labelset_update.labelled_by_user_id = str(account.id)
        # elif isinstance(account, ServiceAccount):
        #     labelset_update.labelled_by_sa_id = str(account.id)

        logger.info("Updating labels", label_updates=labelset_update)
        labelset = labelset_repository.get_or_create(session, work_orm, False)
        old_labelset_data = labelset.get_label_dict(session)
        labelset = labelset_repository.patch(session, labelset, labelset_update, True)
        new_labelset_data = labelset.get_label_dict(session)
        labelset_changes = compare_dicts(old_labelset_data, new_labelset_data)
        del changes.labelset

    updated = work_repository.update(db=session, db_obj=work_orm, obj_in=changes)
    new_work_data = updated.get_dict(session)

    logger.info("Updated work", updated=updated)
    changes_dict = compare_dicts(old_work_data, new_work_data)
    if labelset_changes:
        changes_dict["labelset"] = labelset_changes

    crud.event.create(
        session,
        title="Work updated",
        description=f"Made a change to '{work_orm.title}'",
        info={
            "changes": changes_dict,
            "work_id": work_orm.id,
        },
        account=account,
    )

    return updated


@router.delete(
    "/work/{work_id}",
    response_model=WorkDetail,
    dependencies=[
        Permission("delete", bulk_work_access_control_list),
    ],
)
def delete_work(
    work_orm: Work = Depends(get_work),
    account=Depends(get_current_active_user_or_service_account),
    session: Session = Depends(get_session),
):
    crud.event.create(
        session,
        title="Work deleted",
        description=f"Deleted work '{work_orm.title}'",
        info={
            "work_id": work_orm.id,
        },
        account=account,
    )
    return work_repository.remove(db=session, id=work_orm.id)

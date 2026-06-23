from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.orm import Session
from structlog import get_logger

from app.api.dependencies.security import (
    get_current_active_superuser_or_backend_service_account,
)
from app.db.session import get_session
from app.repositories.labelset_repository import labelset_repository
from app.repositories.work_repository import work_repository
from app.schemas.labelset import LabelSetPatch
from app.services.recommendations import enqueue_debounced_mv_refresh

logger = get_logger()

router = APIRouter(
    tags=["Labelsets"],
    dependencies=[Depends(get_current_active_superuser_or_backend_service_account)],
)


@router.patch("/labelsets")
async def bulk_patch_labelsets(
    patches: list[LabelSetPatch],
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
):
    patched = 0
    unknown = 0
    errors = 0

    for patch in patches:
        work = work_repository.find_by_isbn(session, patch.isbn)
        if not work:
            unknown += 1
            continue

        try:
            labelset = labelset_repository.get_or_create(session, work, False)
            labelset = labelset_repository.patch(
                session, labelset, patch.patch_data, False
            )

            session.commit()
            patched += 1
        except Exception as ex:
            print(ex)
            errors += 1
            continue

    if patched > 0:
        # Enqueue a debounced MV refresh after labelset mutations so the
        # recommendation engine reflects the changes within ~1 minute.
        # Named Cloud Tasks deduplication ensures many rapid writes collapse
        # into a single refresh (see enqueue_debounced_mv_refresh docstring).
        background_tasks.add_task(enqueue_debounced_mv_refresh)

    return {"patched": patched, "unknown": unknown, "errors": errors}

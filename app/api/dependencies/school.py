import uuid

from fastapi import Depends, Path, Query
from sqlalchemy.orm import Session

from app.api.dependencies.async_db_dep import DBSessionDep
from app.db.session import get_session
from app.repositories.school_repository import school_repository


def get_school_from_wriveted_id(
    session: Session = Depends(get_session),
    wriveted_identifier: uuid.UUID = Path(
        ..., description="UUID representing a unique school in the Wriveted database"
    ),
):
    return school_repository.get_by_wriveted_id_or_404(
        db=session, wriveted_id=wriveted_identifier
    )


async def aget_school_from_wriveted_id(
    session: DBSessionDep,
    wriveted_identifier: uuid.UUID = Path(
        ..., description="UUID representing a unique school in the Wriveted database"
    ),
):
    return await school_repository.aget_by_wriveted_id_or_404(
        db=session, wriveted_id=wriveted_identifier
    )


async def aget_school_from_uuid(
    session: DBSessionDep,
    school_uuid: uuid.UUID = Path(..., description="Public school UUID"),
):
    return await school_repository.aget_by_wriveted_id_or_404(
        db=session, wriveted_id=school_uuid
    )


def get_optional_school_from_wriveted_id_query(
    wriveted_identifier: uuid.UUID = Query(
        None, description="UUID representing a unique school in the Wriveted database"
    ),
    session: Session = Depends(get_session),
):
    if wriveted_identifier is None:
        return None
    else:
        return school_repository.get_by_wriveted_id_or_404(
            db=session, wriveted_id=wriveted_identifier
        )

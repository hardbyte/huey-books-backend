import hashlib
from typing import TypeVar
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories import idempotency as repository
from app.services.workspace_errors import WorkspaceConflict

Result = TypeVar("Result", bound=BaseModel)


def fingerprint(operation: str, scope: str, payload: BaseModel) -> str:
    """Bind a stored response to the request that produced it.

    The scope (the resource the operation acts on) is part of the digest so the
    same key replayed against a different resource is a conflict rather than a
    silent success.
    """
    material = f"{operation}\n{scope}\n{payload.model_dump_json()}"
    return hashlib.sha256(material.encode()).hexdigest()


async def replay(
    db: AsyncSession,
    operation: str,
    key: UUID,
    actor_id: UUID,
    digest: str,
    result_type: type[Result],
) -> Result | None:
    """Serialise on the key and return the stored result if this is a retry.

    Callers must hold the returned transaction until they either commit their
    own work or roll back; the advisory lock makes concurrent retries of the
    same key wait rather than duplicate the operation.
    """
    await repository.lock(db, operation, key)
    record = await repository.get(db, operation, key)
    if record is None:
        return None
    if (record.actor_id, record.fingerprint) != (actor_id, digest):
        raise WorkspaceConflict(
            {
                "code": "idempotency_key_reused",
                "message": "This request has changed since it was first sent. "
                "Start a new one instead.",
            }
        )
    return result_type.model_validate(record.response)


def record(
    db: AsyncSession,
    operation: str,
    key: UUID,
    actor_id: UUID,
    digest: str,
    result: BaseModel,
) -> None:
    repository.save(
        db,
        operation=operation,
        key=key,
        actor_id=actor_id,
        fingerprint=digest,
        response=result.model_dump(mode="json"),
    )

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class LibraryChatPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    catalogue_policy: Literal["library_only", "prefer_library"] = "library_only"
    enabled: bool = True
    jokes_enabled: bool = True
    spelling_enabled: bool = True


class LibraryChatUpdate(LibraryChatPolicy):
    expected_revision: int = Field(ge=0)


class LibraryChatDetail(LibraryChatPolicy):
    library_uuid: UUID
    name: str
    revision: int
    available: bool
    unavailable_reason: Literal["disabled", "subscription_required"] | None = None


def pinned_context(state: dict, snapshot: dict | None) -> dict:
    if not snapshot:
        return state
    supplied_context = state.get("context")
    context = dict(supplied_context) if isinstance(supplied_context, dict) else {}
    context.update(
        library_uuid=snapshot["library_uuid"],
        library_name=snapshot["name"],
        school_wriveted_id=snapshot["library_uuid"],
        school_name=snapshot["name"],
        experiments={
            "no_jokes": not snapshot["jokes_enabled"],
            "no-jokes": not snapshot["jokes_enabled"],
        },
    )
    return {**state, "context": context}

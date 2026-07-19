"""Registry of internal API endpoint handlers for direct service calls.

Allows the action processor to bypass HTTP for known internal endpoints,
avoiding authentication requirements and HTTP overhead for anonymous
chatbot sessions.
"""

from typing import Any, Callable, Coroutine, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

logger = get_logger()

# Handlers receive the resolved request body, query params, and the action
# execution context (which carries ``session_user_id`` when the chat session is
# authenticated). ``context`` is optional so handlers that don't need it can
# ignore it.
InternalHandler = Callable[
    [AsyncSession, Dict[str, Any], Dict[str, Any], Optional[Dict[str, Any]]],
    Coroutine[Any, Any, Dict[str, Any]],
]

INTERNAL_HANDLERS: Dict[str, InternalHandler] = {}


def internal_handler(endpoint: str):
    """Decorator to register an internal API handler."""

    def decorator(func: InternalHandler) -> InternalHandler:
        INTERNAL_HANDLERS[endpoint] = func
        return func

    return decorator


@internal_handler("/v1/recommend")
async def handle_recommend(
    db: AsyncSession,
    body: Dict[str, Any],
    query_params: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Direct service call for book recommendations."""
    from app.api.recommendations import get_recommendations_with_fallback
    from app.repositories.school_repository import school_repository
    from app.schemas.recommendations import HueyRecommendationFilter

    data = HueyRecommendationFilter(**body)
    school = None
    if data.wriveted_identifier:
        school = await school_repository.aget_by_wriveted_id_or_404(
            db=db, wriveted_id=data.wriveted_identifier
        )

    try:
        limit = max(1, min(int(query_params.get("limit", 5)), 50))
    except (ValueError, TypeError):
        limit = 5

    # Campaign book bias: soft-boost works from the campaign's themed booklist.
    from app.services.campaigns import get_campaign_boost_work_ids

    boost_work_ids = await get_campaign_boost_work_ids(db, data.booklist_id)

    recommended_books, query_parameters = await get_recommendations_with_fallback(
        asession=db,
        account=None,
        school=school,
        data=data,
        background_tasks=None,
        limit=limit,
        boost_work_ids=boost_work_ids,
    )

    return {
        "count": len(recommended_books),
        "query": query_parameters,
        "books": [book.model_dump(mode="json") for book in recommended_books],
    }


@internal_handler("/v1/onboarding/family")
async def handle_family_onboarding(
    db: AsyncSession,
    body: Dict[str, Any],
    query_params: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Direct service call for family onboarding from the chatflow.

    Creates child reader profiles linked to the authenticated chat session's
    user. The family flow signs the user in before the chat starts, so the
    session carries a ``session_user_id``. When the session is anonymous we
    create nothing — profiles must belong to an account — and ask the user to
    sign in.
    """
    from uuid import UUID

    from app.models.user import User
    from app.services.onboarding_service import (
        create_linked_family_readers,
        normalise_chatflow_child,
    )

    session_user_id = (context or {}).get("session_user_id")
    if not session_user_id:
        logger.warning("Family onboarding attempted without an authenticated session")
        return {
            "children_created": 0,
            "message": "Please sign in to save reader profiles.",
        }

    raw_children = body.get("children", [])
    if not isinstance(raw_children, list):
        return {"children_created": 0, "message": "Invalid children data"}

    # Cap at 10 children per request, dropping malformed entries.
    children = []
    for child in raw_children[:10]:
        normalised = normalise_chatflow_child(child)
        if normalised is not None:
            children.append(normalised)

    user = await db.get(User, UUID(str(session_user_id)))
    if user is None:
        logger.warning(
            "Family onboarding session user not found",
            session_user_id=session_user_id,
        )
        return {"children_created": 0, "message": "Account not found."}

    parent_name = str(body.get("parent_name") or user.name or "")[:200]

    children_created = await create_linked_family_readers(
        db,
        user=user,
        parent_name=parent_name,
        children=children,
    )

    return {
        "children_created": children_created,
        "message": f"{children_created} reader profile(s) created.",
    }

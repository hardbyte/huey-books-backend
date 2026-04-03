"""Registry of internal API endpoint handlers for direct service calls.

Allows the action processor to bypass HTTP for known internal endpoints,
avoiding authentication requirements and HTTP overhead for anonymous
chatbot sessions.
"""

from typing import Any, Callable, Coroutine, Dict

from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

logger = get_logger()

InternalHandler = Callable[
    [AsyncSession, Dict[str, Any], Dict[str, Any]],
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
    recommended_books, query_parameters = await get_recommendations_with_fallback(
        asession=db,
        account=None,
        school=school,
        data=data,
        background_tasks=None,
        limit=limit,
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
) -> Dict[str, Any]:
    """Direct service call for family onboarding from chatflow.

    Since chatflow sessions are anonymous, this creates the child readers
    without promoting a user. The parent account linkage happens when
    the user signs in on the frontend and claims the session.
    """
    from app.models.public_reader import PublicReader

    children_created = 0
    for child in body.get("children", []):
        if not isinstance(child, dict) or not child.get("name"):
            continue
        age = child.get("age")
        if isinstance(age, str):
            try:
                age = int(age)
            except ValueError:
                age = None
        reader = PublicReader(
            name=child["name"],
            first_name=child["name"],
            huey_attributes={
                "age": age,
                "reading_ability": child.get("reading_ability"),
            },
        )
        db.add(reader)
        children_created += 1

    if children_created > 0:
        await db.flush()

    return {
        "children_created": children_created,
        "message": f"{children_created} reader profile(s) created.",
    }

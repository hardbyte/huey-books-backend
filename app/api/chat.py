import secrets
from typing import Optional
from uuid import UUID

from fastapi import (
    APIRouter,
    Body,
    Depends,
    HTTPException,
    Path,
    Query,
    Response,
    Security,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette import status
from structlog import get_logger

from app import crud
from app.api.common.pagination import PaginatedQueryParams
from app.api.dependencies.async_db_dep import DBSessionDep
from app.api.dependencies.csrf import CSRFProtected
from app.api.dependencies.security import (
    get_current_active_superuser_or_backend_service_account,
    get_current_active_user,
    get_optional_authenticated_user,
)
from app.config import get_settings
from app.crud.cms import CRUDConversationSession
from app.models import User
from app.models.cms import ChatTheme, SessionStatus
from app.repositories.chat_repository import chat_repo
from app.schemas.cms import (
    ConversationHistoryResponse,
    InteractionCreate,
    InteractionResponse,
    SessionCreate,
    SessionDetail,
    SessionStartResponse,
    SessionStateUpdate,
)
from app.schemas.pagination import Pagination
from app.security.csrf import generate_csrf_token, set_secure_session_cookie
from app.services.chat_runtime import FlowNotFoundError, chat_runtime

logger = get_logger()

router = APIRouter(
    tags=["Chat Runtime"],
)


@router.post(
    "/start", response_model=SessionStartResponse, status_code=status.HTTP_201_CREATED
)
async def start_conversation(
    response: Response,
    session: DBSessionDep,
    session_data: SessionCreate = Body(...),
    current_user: Optional[User] = Security(get_optional_authenticated_user),
    settings=Depends(get_settings),
):
    """Start a new conversation session."""

    # Generate session token
    session_token = secrets.token_urlsafe(32)

    try:
        # SECURITY: Prevent user impersonation - validate user_id against authentication
        user_id_for_session: Optional[UUID] = None

        if current_user:
            # If authenticated, user ID comes from verified token
            user_id_for_session = current_user.id
            # If user_id also provided in body, it MUST match authenticated user
            if session_data.user_id and session_data.user_id != current_user.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Provided user_id does not match authenticated user.",
                )
        else:
            # If anonymous, request body CANNOT specify user_id to prevent impersonation
            if session_data.user_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Cannot specify a user_id for an anonymous session.",
                )
            user_id_for_session = None  # Explicitly anonymous

        # Create session using runtime
        conversation_session = await chat_runtime.start_session(
            session,
            flow_id=session_data.flow_id,
            user_id=user_id_for_session,
            session_token=session_token,
            initial_state=session_data.initial_state,
        )

        # Resolve school name from school_wriveted_id if not already set
        session_state = conversation_session.state or {}
        ctx = session_state.get("context", {})
        school_wriveted_id = ctx.get("school_wriveted_id")
        if school_wriveted_id and not ctx.get("school_name"):
            try:
                from app.repositories.school_repository import school_repository

                school_obj = await school_repository.aget_by_wriveted_id_or_404(
                    db=session, wriveted_id=school_wriveted_id
                )
                ctx["school_name"] = school_obj.name
                session_state["context"] = ctx
                await chat_repo.update_session_state(
                    session,
                    session_id=conversation_session.id,
                    state_updates=session_state,
                    expected_revision=conversation_session.revision,
                )
                # Refresh session to pick up updated state
                refreshed = await chat_repo.get_session_by_id(
                    session, conversation_session.id
                )
                if refreshed:
                    conversation_session = refreshed
            except Exception as e:
                logger.warning(
                    "Could not resolve school name",
                    school_wriveted_id=school_wriveted_id,
                    error=str(e),
                )

        # Get initial node
        initial_node = await chat_runtime.get_initial_node(
            session, session_data.flow_id, conversation_session
        )

        # Load theme if flow has one configured
        theme_id = None
        theme_response = None
        flow = await crud.flow.aget(session, session_data.flow_id)
        if flow and flow.flow_data:
            # Theme ID can be in flow_data.theme_id or info.theme_id
            theme_id_str = flow.flow_data.get("theme_id") or flow.info.get("theme_id")
            if theme_id_str:
                try:
                    theme_id = (
                        UUID(theme_id_str)
                        if isinstance(theme_id_str, str)
                        else theme_id_str
                    )
                    # Load the theme
                    theme_result = await session.execute(
                        select(ChatTheme).where(
                            ChatTheme.id == theme_id, ChatTheme.is_active.is_(True)
                        )
                    )
                    theme = theme_result.scalar_one_or_none()
                    if theme:
                        # Return full theme object for frontend
                        theme_response = {
                            "id": str(theme.id),
                            "name": theme.name,
                            "config": theme.config,
                            "logo_url": theme.logo_url,
                            "avatar_url": theme.avatar_url,
                        }
                except (ValueError, TypeError):
                    logger.warning(
                        "Invalid theme_id in flow",
                        flow_id=session_data.flow_id,
                        theme_id=theme_id_str,
                    )

        # Set secure session cookie and CSRF token
        csrf_token = generate_csrf_token()

        # Set CSRF token cookie (httponly=False required for double-submit pattern)
        # Production: SameSite=none allows cross-origin cookie sending (requires Secure)
        # Debug: SameSite=lax for cross-port local development without HTTPS
        response.set_cookie(
            "csrf_token",
            csrf_token,
            httponly=False,
            samesite="lax" if settings.DEBUG else "none",
            secure=not settings.DEBUG,
            max_age=3600 * 24,  # 24 hours
        )

        # Set session cookie for additional security
        set_secure_session_cookie(
            response,
            "chat_session",
            session_token,
            max_age=3600 * 8,  # 8 hours
            debug=settings.DEBUG,
        )

        logger.info(
            "Started conversation session",
            session_id=conversation_session.id,
            flow_id=session_data.flow_id,
            user_id=conversation_session.user_id,
            csrf_token_set=True,
        )

        return SessionStartResponse(
            session_id=conversation_session.id,
            session_token=session_token,
            csrf_token=csrf_token,  # Include in response for cross-origin scenarios
            next_node=initial_node,
            theme_id=theme_id,
            theme=theme_response,
            flow_name=flow.name if flow else None,
        )

    except HTTPException:
        # Re-raise HTTPExceptions (like our security validation errors)
        raise
    except FlowNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error("Error starting conversation", error=str(e), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error starting conversation",
        )


@router.get("/sessions/{session_token}", response_model=SessionDetail)
async def get_session_state(
    session: DBSessionDep,
    session_token: str = Path(description="Session token"),
):
    """Get current session state."""

    conversation_session = await chat_repo.get_session_by_token(
        session, session_token=session_token
    )

    if not conversation_session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found"
        )

    return conversation_session


@router.post("/sessions/{session_token}/interact", response_model=InteractionResponse)
async def interact_with_session(
    session: DBSessionDep,
    session_token: str = Path(description="Session token"),
    interaction: InteractionCreate = Body(...),
    _csrf_protected: bool = CSRFProtected,
):
    """Send input to conversation session and get response."""

    # Get session
    conversation_session = await chat_repo.get_session_by_token(
        session, session_token=session_token
    )

    if not conversation_session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found"
        )

    if conversation_session.status != SessionStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Session is not active"
        )

    try:
        # Process the interaction through runtime
        response = await chat_runtime.process_interaction(
            session,
            conversation_session,
            user_input=interaction.input,
            input_type=interaction.input_type,
        )

        logger.info(
            "Processed interaction",
            session_id=conversation_session.id,
            input_type=interaction.input_type,
            response_keys=list(response.keys()),
            session_updated=response.get("session_updated"),
        )

        return InteractionResponse(
            messages=response.get("messages", []),
            input_request=response.get("input_request"),
            session_ended=response.get("session_ended", False),
            current_node_id=response.get("current_node_id"),
            session_updated=response.get("session_updated"),
            wait_for_acknowledgment=response.get("wait_for_acknowledgment", False),
        )

    except IntegrityError:
        # Handle concurrency conflicts
        logger.warning("Session state conflict", session_id=conversation_session.id)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Session state has been modified by another process",
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(
            "Error processing interaction",
            error=str(e),
            session_id=conversation_session.id,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error processing interaction",
        )


@router.post("/sessions/{session_token}/end")
async def end_session(
    session: DBSessionDep,
    session_token: str = Path(description="Session token"),
    _csrf_protected: bool = CSRFProtected,
):
    """End conversation session."""

    conversation_session = await chat_repo.get_session_by_token(
        session, session_token=session_token
    )

    if not conversation_session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found"
        )

    if conversation_session.status != SessionStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Session is not active"
        )

    try:
        # End the session
        await chat_repo.end_session(
            session, session_id=conversation_session.id, status=SessionStatus.COMPLETED
        )

        logger.info("Ended conversation session", session_id=conversation_session.id)

        return {"message": "Session ended successfully"}

    except Exception as e:
        logger.error("Error ending session", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error ending session",
        )


@router.get(
    "/sessions/{session_token}/history", response_model=ConversationHistoryResponse
)
async def get_conversation_history(
    session: DBSessionDep,
    session_token: str = Path(description="Session token"),
    pagination: PaginatedQueryParams = Depends(),
):
    """Get conversation history for session."""

    conversation_session = await chat_repo.get_session_by_token(
        session, session_token=session_token
    )

    if not conversation_session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found"
        )

    history = await chat_repo.get_session_history(
        session,
        session_id=conversation_session.id,
        skip=pagination.skip,
        limit=pagination.limit,
    )

    return ConversationHistoryResponse(
        pagination=Pagination(**pagination.to_dict(), total=None), data=history
    )


@router.patch("/sessions/{session_token}/state")
async def update_session_state(
    session: DBSessionDep,
    session_token: str = Path(description="Session token"),
    state_update: SessionStateUpdate = Body(...),
    _csrf_protected: bool = CSRFProtected,
):
    """Update session state variables."""

    conversation_session = await chat_repo.get_session_by_token(
        session, session_token=session_token
    )

    if not conversation_session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found"
        )

    if conversation_session.status != SessionStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Session is not active"
        )

    try:
        # Update session state with concurrency control
        updated_session = await chat_repo.update_session_state(
            session,
            session_id=conversation_session.id,
            state_updates=state_update.updates,
            expected_revision=state_update.expected_revision,
        )

        logger.info(
            "Updated session state",
            session_id=conversation_session.id,
            updates=list(state_update.updates.keys()),
        )

        return {
            "message": "Session state updated",
            "state": updated_session.state,
            "revision": updated_session.revision,
        }

    except IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Session state has been modified by another process",
        )
    except Exception as e:
        logger.error("Error updating session state", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error updating session state",
        )


# Admin endpoints for session management
@router.get(
    "/admin/sessions",
    dependencies=[Security(get_current_active_superuser_or_backend_service_account)],
)
async def list_sessions(
    session: DBSessionDep,
    user_id: Optional[UUID] = Query(None, description="Filter by user ID"),
    status: Optional[SessionStatus] = Query(None, description="Filter by status"),
    pagination: PaginatedQueryParams = Depends(),
):
    """List conversation sessions (admin only)."""

    if user_id:
        sessions = await crud.conversation_session.aget_by_user(
            session,
            user_id=user_id,
            status=status,
            skip=pagination.skip,
            limit=pagination.limit,
        )
    else:
        # Get all sessions with filters
        sessions = await crud.conversation_session.aget_multi(
            session, skip=pagination.skip, limit=pagination.limit
        )

    return {
        "pagination": Pagination(**pagination.to_dict(), total=None),
        "data": sessions,
    }


@router.delete(
    "/admin/sessions/{session_id}",
    dependencies=[Security(get_current_active_user)],
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_session(
    session: DBSessionDep,
    session_id: UUID = Path(description="Session ID"),
):
    """Delete conversation session and its history (admin only)."""

    conversation_session = await crud.conversation_session.aget(session, session_id)
    if not conversation_session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found"
        )

    # This will cascade delete the history due to foreign key constraints
    conversation_crud: CRUDConversationSession = crud.conversation_session  # type: ignore
    await conversation_crud.aremove(session, id=session_id)

    logger.info("Deleted conversation session", session_id=session_id)

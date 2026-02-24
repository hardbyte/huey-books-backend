import asyncio
import base64
import hashlib
import json
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple
from uuid import UUID

from sqlalchemy import and_, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from structlog import get_logger

from app.models.cms import (
    ConversationHistory,
    ConversationSession,
    FlowConnection,
    FlowNode,
    IdempotencyRecord,
    InteractionType,
    SessionStatus,
    TaskExecutionStatus,
)

logger = get_logger()
_UNSET = object()


class ChatRepository:
    """Repository for chat-related database operations with concurrency support."""

    def __init__(self) -> None:
        self.logger = logger

    async def get_session_by_token(
        self, db: AsyncSession, session_token: str
    ) -> Optional[ConversationSession]:
        """Get session by token with eager loading of relationships."""
        result = await db.scalars(
            select(ConversationSession)
            .where(ConversationSession.session_token == session_token)
            .options(
                selectinload(ConversationSession.flow),
                selectinload(ConversationSession.user),
            )
        )
        return result.first()

    async def create_session(
        self,
        db: AsyncSession,
        *,
        flow_id: UUID,
        user_id: Optional[UUID] = None,
        session_token: str,
        initial_state: Optional[Dict[str, Any]] = None,
        meta_data: Optional[Dict[str, Any]] = None,
        flow_version: Optional[str] = None,
        trace_enabled: Optional[bool] = None,
        trace_level: Optional[str] = None,
    ) -> ConversationSession:
        """Create a new conversation session with initial state."""
        # Ensure standard scope namespaces always exist so CEL
        # expressions like has(context.foo) don't throw when
        # the top-level scope key is missing entirely.
        state = {"context": {}, "temp": {}, "user": {}, "system": {}}
        if initial_state:
            for key, value in initial_state.items():
                if key in state and isinstance(value, dict):
                    state[key].update(value)
                else:
                    state[key] = value
        state_hash = self._calculate_state_hash(state)

        session = ConversationSession(
            flow_id=flow_id,
            user_id=user_id,
            session_token=session_token,
            state=state,
            info=meta_data or {},
            status=SessionStatus.ACTIVE,
            revision=1,
            state_hash=state_hash,
            flow_version=flow_version,
            trace_enabled=bool(trace_enabled) if trace_enabled is not None else False,
            trace_level=trace_level or "standard",
        )

        db.add(session)
        await db.commit()
        await db.refresh(session)

        return session

    async def update_session_state(
        self,
        db: AsyncSession,
        *,
        session_id: UUID,
        state_updates: Dict[str, Any],
        current_node_id: Optional[str] = None,
        current_flow_id: Any = _UNSET,
        expected_revision: Optional[int] = None,
    ) -> ConversationSession:
        """Update session state with optimistic concurrency control."""
        # Get current session
        result = await db.scalars(
            select(ConversationSession)
            .where(ConversationSession.id == session_id)
            .with_for_update()  # Lock the row
        )
        session = result.first()

        if not session:
            raise ValueError("Session not found")

        # Check revision if provided (optimistic locking)
        if expected_revision is not None and session.revision != expected_revision:
            raise IntegrityError(
                "Session state has been modified by another process",
                params=None,
                orig=ValueError("Concurrent modification detected"),
            )

        # Update state with support for nested updates
        # Create a copy to avoid modifying the original
        current_state = dict(session.state or {})
        self._deep_merge_state(current_state, state_updates)

        # Calculate new state hash
        new_state_hash = self._calculate_state_hash(current_state)

        # Update session
        # For JSON/JSONB columns, we need to reassign the value to trigger SQLAlchemy's change detection
        # or use flag_modified
        from sqlalchemy.orm.attributes import flag_modified

        session.state = current_state
        flag_modified(session, "state")  # Explicitly mark the state field as modified

        session.state_hash = new_state_hash
        session.revision = session.revision + 1
        session.last_activity_at = datetime.utcnow()

        if current_node_id:
            session.current_node_id = current_node_id

        if current_flow_id is not _UNSET:
            session.current_flow_id = current_flow_id

        # Debug logging for state persistence (commented out for performance)
        # self.logger.info(
        #     "Session state before commit",
        #     session_id=session_id,
        #     state=session.state,
        #     revision=session.revision,
        # )

        await db.commit()
        await db.refresh(session)

        # self.logger.info(
        #     "Session state after refresh",
        #     session_id=session_id,
        #     state=session.state,
        #     revision=session.revision,
        # )

        return session

    async def end_session(
        self,
        db: AsyncSession,
        *,
        session_id: UUID,
        status: SessionStatus = SessionStatus.COMPLETED,
    ) -> ConversationSession:
        """End a conversation session."""
        result = await db.scalars(
            select(ConversationSession).where(ConversationSession.id == session_id)
        )
        session = result.first()

        if not session:
            raise ValueError("Session not found")

        session.status = status
        session.ended_at = datetime.utcnow()
        session.last_activity_at = datetime.utcnow()

        await db.commit()
        await db.refresh(session)

        return session

    async def add_interaction_history(
        self,
        db: AsyncSession,
        *,
        session_id: UUID,
        node_id: str,
        interaction_type: InteractionType,
        content: Dict[str, Any],
    ) -> ConversationHistory:
        """Add an interaction to the conversation history."""
        history_entry = ConversationHistory(
            session_id=session_id,
            node_id=node_id,
            interaction_type=interaction_type,
            content=content,
        )

        db.add(history_entry)
        await db.commit()
        await db.refresh(history_entry)

        return history_entry

    async def get_session_history(
        self, db: AsyncSession, *, session_id: UUID, skip: int = 0, limit: int = 100
    ) -> list[ConversationHistory]:
        """Get conversation history for a session."""
        result = await db.scalars(
            select(ConversationHistory)
            .where(ConversationHistory.session_id == session_id)
            .order_by(ConversationHistory.created_at)
            .offset(skip)
            .limit(limit)
        )
        return list(result.all())

    async def get_flow_node(
        self, db: AsyncSession, *, flow_id: UUID, node_id: str
    ) -> Optional[FlowNode]:
        """Get a specific flow node."""
        result = await db.scalars(
            select(FlowNode).where(
                and_(FlowNode.flow_id == flow_id, FlowNode.node_id == node_id)
            )
        )
        return result.first()

    async def get_node_connections(
        self, db: AsyncSession, *, flow_id: UUID, source_node_id: str
    ) -> list[FlowConnection]:
        """Get all connections from a specific node."""
        result = await db.scalars(
            select(FlowConnection)
            .where(
                and_(
                    FlowConnection.flow_id == flow_id,
                    FlowConnection.source_node_id == source_node_id,
                )
            )
            .order_by(FlowConnection.connection_type)
        )
        return list(result.all())

    async def get_active_sessions_count(
        self,
        db: AsyncSession,
        *,
        flow_id: Optional[UUID] = None,
        user_id: Optional[UUID] = None,
    ) -> int:
        """Get count of active sessions with optional filters."""
        query = select(func.count(ConversationSession.id)).where(
            ConversationSession.status == SessionStatus.ACTIVE
        )

        if flow_id:
            query = query.where(ConversationSession.flow_id == flow_id)

        if user_id:
            query = query.where(ConversationSession.user_id == user_id)

        result = await db.scalar(query)
        return result or 0

    async def cleanup_abandoned_sessions(
        self, db: AsyncSession, *, inactive_hours: int = 24
    ) -> int:
        """Mark inactive sessions as abandoned."""
        cutoff_time = datetime.utcnow() - timedelta(hours=inactive_hours)

        # Update sessions that haven't had activity
        result = await db.execute(
            update(ConversationSession)
            .where(
                and_(
                    ConversationSession.status == SessionStatus.ACTIVE,
                    ConversationSession.last_activity_at < cutoff_time,
                )
            )
            .values(status=SessionStatus.ABANDONED, ended_at=datetime.utcnow())
        )

        await db.commit()
        return result.rowcount

    def _deep_merge_state(self, target: Dict[str, Any], source: Dict[str, Any]) -> None:
        """
        Deep merge source dictionary into target dictionary.

        This handles nested dictionaries properly, so that:
        target = {"temp": {"existing": "value"}}
        source = {"temp": {"name": "John"}}

        Results in: {"temp": {"existing": "value", "name": "John"}}
        """
        # Deep merge logging (commented out for performance)
        # self.logger.info(
        #     "Deep merge state",
        #     target_before=target.copy(),
        #     source=source,
        # )
        for key, value in source.items():
            if (
                key in target
                and isinstance(target[key], dict)
                and isinstance(value, dict)
            ):
                # Both are dicts, recursively merge
                self._deep_merge_state(target[key], value)
            else:
                # Overwrite or add new key
                target[key] = value

        # self.logger.info(
        #     "Deep merge complete",
        #     target_after=target,
        # )

    def _calculate_state_hash(self, state: Dict[str, Any]) -> str:
        """Calculate SHA-256 hash of session state for integrity checking."""
        state_json = json.dumps(state, sort_keys=True, separators=(",", ":"))
        hash_bytes = hashlib.sha256(state_json.encode("utf-8")).digest()
        return base64.b64encode(hash_bytes).decode("ascii")  # 44 characters

    def generate_idempotency_key(
        self, session_id: UUID, node_id: str, revision: int
    ) -> str:
        """Generate idempotency key for async operations."""
        return f"{session_id}:{node_id}:{revision}"

    async def validate_task_revision(
        self, db: AsyncSession, session_id: UUID, expected_revision: int
    ) -> bool:
        """Validate that a task's revision matches current session revision."""
        result = await db.scalar(
            select(ConversationSession.revision).where(
                ConversationSession.id == session_id
            )
        )

        if result is None:
            self.logger.warning(
                "Session not found during revision validation", session_id=session_id
            )
            return False

        if result != expected_revision:
            self.logger.warning(
                "Task revision mismatch - discarding stale task",
                session_id=session_id,
                expected_revision=expected_revision,
                current_revision=result,
            )
            return False

        return True

    async def get_session_by_id(
        self, db: AsyncSession, session_id: UUID
    ) -> Optional[ConversationSession]:
        """Get session by ID with eager loading of relationships."""
        result = await db.scalars(
            select(ConversationSession)
            .where(ConversationSession.id == session_id)
            .options(
                selectinload(ConversationSession.flow),
                selectinload(ConversationSession.user),
            )
        )
        return result.first()

    async def acquire_idempotency_lock(
        self,
        db: AsyncSession,
        idempotency_key: str,
        session_id: UUID,
        node_id: str,
        session_revision: int,
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Atomically acquire idempotency lock or return existing result.

        Returns:
            (acquired, result_data) where:
            - acquired=True means this is first execution, proceed with task
            - acquired=False means task was already processed, result_data contains response
        """
        try:
            record = IdempotencyRecord(
                idempotency_key=idempotency_key,
                status=TaskExecutionStatus.PROCESSING,
                session_id=session_id,
                node_id=node_id,
                session_revision=session_revision,
            )

            db.add(record)
            await db.commit()

            return True, None

        except IntegrityError:
            await db.rollback()

            result = await db.scalars(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.idempotency_key == idempotency_key
                )
            )
            existing = result.first()

            if not existing:
                await asyncio.sleep(0.1)
                return await self.acquire_idempotency_lock(
                    db, idempotency_key, session_id, node_id, session_revision
                )

            if existing.status == TaskExecutionStatus.PROCESSING:
                for _ in range(30):
                    await asyncio.sleep(1)
                    await db.refresh(existing)
                    if existing.status != TaskExecutionStatus.PROCESSING:
                        break  # type: ignore[unreachable]

            return False, {
                "status": existing.status.value,
                "result_data": existing.result_data,
                "error_message": existing.error_message,
                "idempotency_key": idempotency_key,
            }

    async def complete_idempotency_record(
        self,
        db: AsyncSession,
        idempotency_key: str,
        success: bool,
        result_data: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """Mark idempotency record as completed."""
        await db.execute(
            update(IdempotencyRecord)
            .where(IdempotencyRecord.idempotency_key == idempotency_key)
            .values(
                status=TaskExecutionStatus.COMPLETED
                if success
                else TaskExecutionStatus.FAILED,
                result_data=result_data,
                error_message=error_message,
                completed_at=func.current_timestamp(),
            )
        )
        await db.commit()


# Create singleton instance
chat_repo = ChatRepository()


def reset_chat_repository() -> None:
    """Reset the global chat repository instance for testing."""
    global chat_repo
    chat_repo = ChatRepository()

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from fastapi_permissions import All, Allow  # type: ignore[import-untyped]
from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.schemas import CaseInsensitiveStringEnum

if TYPE_CHECKING:
    from app.models.user import User


class ContentType(CaseInsensitiveStringEnum):
    JOKE = "joke"
    QUESTION = "question"
    FACT = "fact"
    QUOTE = "quote"
    MESSAGE = "message"
    PROMPT = "prompt"


class ExecutionContext(CaseInsensitiveStringEnum):
    FRONTEND = "frontend"
    BACKEND = "backend"
    MIXED = "mixed"


class NodeType(CaseInsensitiveStringEnum):
    MESSAGE = "message"
    QUESTION = "question"
    CONDITION = "condition"
    ACTION = "action"
    WEBHOOK = "webhook"
    COMPOSITE = "composite"
    SCRIPT = "script"


class ConnectionType(CaseInsensitiveStringEnum):
    DEFAULT = "default"
    OPTION_0 = "$0"
    OPTION_1 = "$1"
    SUCCESS = "success"
    FAILURE = "failure"


class InteractionType(CaseInsensitiveStringEnum):
    MESSAGE = "message"
    INPUT = "input"
    ACTION = "action"


class TaskExecutionStatus(CaseInsensitiveStringEnum):
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class SessionStatus(CaseInsensitiveStringEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class ContentStatus(CaseInsensitiveStringEnum):
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class ContentVisibility(CaseInsensitiveStringEnum):
    """Visibility levels for CMS content and flows.

    Controls who can access content:
    - PRIVATE: Only visible to creator and school admins
    - SCHOOL: Visible to all users in the creating school
    - PUBLIC: Visible to all authenticated users globally
    - WRIVETED: Wriveted-curated global content (admin-only editing)
    """

    PRIVATE = "private"
    SCHOOL = "school"
    PUBLIC = "public"
    WRIVETED = "wriveted"


class CMSContent(Base):
    __tablename__ = "cms_content"  # type: ignore[assignment]

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
        primary_key=True,
    )

    type: Mapped[ContentType] = mapped_column(
        Enum(ContentType, name="enum_cms_content_type"), nullable=False, index=True
    )

    content: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSONB),
        nullable=False,  # type: ignore[arg-type]
    )

    info: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSONB),  # type: ignore[arg-type]
        nullable=False,
        server_default=text("'{}'::json"),
    )

    tags: Mapped[list[str]] = mapped_column(
        MutableList.as_mutable(ARRAY(String)),
        nullable=False,
        server_default=text("'{}'::text[]"),
        index=True,
    )

    # Full-text search document maintained by trigger (see app/db/functions.py & app/db/triggers.py)
    # Declared here to keep schema in SQLAlchemy; populated by trigger on INSERT/UPDATE
    search_document: Mapped[Optional[str]] = mapped_column(TSVECTOR, nullable=True)

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), index=True
    )

    # Content workflow status
    status: Mapped[ContentStatus] = mapped_column(
        Enum(ContentStatus, name="enum_cms_content_status"),
        nullable=False,
        server_default=text("'draft'"),
        index=True,
    )

    # Version tracking
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", name="fk_content_created_by", ondelete="SET NULL"),
        nullable=True,
    )
    created_by_user: Mapped[Optional["User"]] = relationship(
        "User", foreign_keys=[created_by], lazy="select"
    )

    # School-scoped ownership and visibility
    school_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey(
            "schools.wriveted_identifier",
            name="fk_content_school",
            ondelete="CASCADE",
        ),
        nullable=True,
        index=True,
    )

    visibility: Mapped[ContentVisibility] = mapped_column(
        Enum(
            ContentVisibility,
            name="enum_cms_content_visibility",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        server_default=text("'wriveted'"),
        index=True,
    )

    # Relationships
    variants: Mapped[list["CMSContentVariant"]] = relationship(
        "CMSContentVariant", back_populates="content", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<CMSContent-{self.type} id={self.id}>"

    def __acl__(self) -> List[tuple[Any, str, str]]:
        """Defines who can do what to the content"""
        policies = [
            (Allow, "role:admin", All),
            (Allow, "role:user", "read"),
        ]
        return policies

    # SQLAlchemy index declaration for GIN index on search_document
    __table_args__ = (
        Index(
            "ix_cms_content_search_document",
            "search_document",
            postgresql_using="gin",
        ),
    )


class CMSContentVariant(Base):
    __tablename__ = "cms_content_variants"  # type: ignore[assignment]

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
        primary_key=True,
    )

    content_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cms_content.id", name="fk_variant_content", ondelete="CASCADE"),
        nullable=False,
    )

    variant_key: Mapped[str] = mapped_column(String(100), nullable=False)

    variant_data: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSONB),
        nullable=False,  # type: ignore[arg-type]
    )

    weight: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("100")
    )

    conditions: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSONB),  # type: ignore[arg-type]
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )

    performance_data: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSON),  # type: ignore[arg-type]
        nullable=False,
        server_default=text("'{}'::json"),
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )

    # Relationships
    content: Mapped["CMSContent"] = relationship(
        "CMSContent", back_populates="variants"
    )

    __table_args__ = (
        UniqueConstraint("content_id", "variant_key", name="uq_content_variant_key"),
    )

    def __repr__(self) -> str:
        return f"<CMSContentVariant {self.variant_key} for {self.content_id}>"


class FlowDefinition(Base):
    __tablename__ = "flow_definitions"  # type: ignore[assignment]

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
        primary_key=True,
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)

    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    version: Mapped[str] = mapped_column(String(50), nullable=False)

    flow_data: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSONB),
        nullable=False,  # type: ignore[arg-type]
    )

    entry_node_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    info: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSONB),  # type: ignore[arg-type]
        nullable=False,
        server_default=text("'{}'::json"),
    )

    is_published: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), index=True
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Trace retention configuration (days to keep execution traces)
    retention_days: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("30")
    )

    # Flow-level tracing configuration
    trace_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    trace_sample_rate: Mapped[float] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("100"),  # Percentage (0-100)
    )

    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", name="fk_flow_created_by", ondelete="SET NULL"),
        nullable=True,
    )

    published_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", name="fk_flow_published_by", ondelete="SET NULL"),
        nullable=True,
    )

    # School-scoped ownership and visibility
    school_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey(
            "schools.wriveted_identifier",
            name="fk_flow_school",
            ondelete="CASCADE",
        ),
        nullable=True,
        index=True,
    )

    visibility: Mapped[ContentVisibility] = mapped_column(
        Enum(
            ContentVisibility,
            name="enum_cms_content_visibility",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        server_default=text("'wriveted'"),
        index=True,
    )

    # Relationships
    created_by_user: Mapped[Optional["User"]] = relationship(
        "User", foreign_keys=[created_by], lazy="select"
    )
    published_by_user: Mapped[Optional["User"]] = relationship(
        "User", foreign_keys=[published_by], lazy="select"
    )

    nodes: Mapped[list["FlowNode"]] = relationship(
        "FlowNode", back_populates="flow", cascade="all, delete-orphan"
    )

    connections: Mapped[list["FlowConnection"]] = relationship(
        "FlowConnection", back_populates="flow", cascade="all, delete-orphan"
    )

    sessions: Mapped[list["ConversationSession"]] = relationship(
        "ConversationSession",
        back_populates="flow",
        foreign_keys="[ConversationSession.flow_id]",
    )

    analytics: Mapped[list["ConversationAnalytics"]] = relationship(
        "ConversationAnalytics", back_populates="flow"
    )

    def __repr__(self) -> str:
        return f"<FlowDefinition {self.name} v{self.version}>"

    @property
    def contract(self) -> Optional[Dict[str, Any]]:
        """Get the flow contract from info.contract."""
        info = self.info or {}
        contract = info.get("contract") or {}
        return contract or None

    @contract.setter
    def contract(self, value: Optional[Dict[str, Any]]) -> None:
        """Set the flow contract in info.contract."""
        info = dict(self.info or {})
        if value is None:
            info.pop("contract", None)
        else:
            info["contract"] = value
        self.info = info

    def __acl__(self) -> List[tuple[Any, str, str]]:
        """Defines who can do what to the flow"""
        policies = [
            (Allow, "role:admin", All),
            (Allow, "role:user", "read"),
        ]
        return policies


class FlowNode(Base):
    __tablename__ = "flow_nodes"  # type: ignore[assignment]

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
        primary_key=True,
    )

    flow_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("flow_definitions.id", name="fk_node_flow", ondelete="CASCADE"),
        nullable=False,
    )

    node_id: Mapped[str] = mapped_column(String(255), nullable=False)

    node_type: Mapped[NodeType] = mapped_column(
        Enum(NodeType, name="enum_flow_node_type"), nullable=False, index=True
    )

    execution_context: Mapped[ExecutionContext] = mapped_column(
        Enum(ExecutionContext, name="enum_execution_context"),
        nullable=False,
        server_default=text("'backend'"),
        index=True,
    )

    template: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    content: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSONB),
        nullable=False,  # type: ignore[arg-type]
    )

    position: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSONB),  # type: ignore[arg-type]
        nullable=False,
        server_default=text('\'{"x": 0, "y": 0}\'::json'),
    )

    info: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSONB),  # type: ignore[arg-type]
        nullable=False,
        server_default=text("'{}'::json"),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    # Relationships
    flow: Mapped["FlowDefinition"] = relationship(
        "FlowDefinition", back_populates="nodes"
    )

    source_connections: Mapped[list["FlowConnection"]] = relationship(
        "FlowConnection",
        primaryjoin="and_(FlowNode.flow_id == FlowConnection.flow_id, FlowNode.node_id == FlowConnection.source_node_id)",
        back_populates="source_node",
        cascade="all, delete-orphan",
        overlaps="connections",
    )

    target_connections: Mapped[list["FlowConnection"]] = relationship(
        "FlowConnection",
        primaryjoin="and_(FlowNode.flow_id == FlowConnection.flow_id, FlowNode.node_id == FlowConnection.target_node_id)",
        back_populates="target_node",
        overlaps="connections,source_connections",
    )

    __table_args__ = (UniqueConstraint("flow_id", "node_id", name="uq_flow_node_id"),)

    def __repr__(self) -> str:
        return f"<FlowNode {self.node_id} ({self.node_type})>"


class FlowConnection(Base):
    __tablename__ = "flow_connections"  # type: ignore[assignment]

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
        primary_key=True,
    )

    flow_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "flow_definitions.id", name="fk_connection_flow", ondelete="CASCADE"
        ),
        nullable=False,
        index=True,
    )

    source_node_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    target_node_id: Mapped[str] = mapped_column(String(255), nullable=False)

    connection_type: Mapped[ConnectionType] = mapped_column(
        Enum(ConnectionType, name="enum_flow_connection_type"), nullable=False
    )

    conditions: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSONB),  # type: ignore[arg-type]
        nullable=False,
        server_default=text("'{}'::json"),
    )

    info: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSONB),  # type: ignore[arg-type]
        nullable=False,
        server_default=text("'{}'::json"),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )

    # Relationships
    flow: Mapped["FlowDefinition"] = relationship(
        "FlowDefinition",
        back_populates="connections",
        overlaps="source_connections,target_connections",
    )

    source_node: Mapped["FlowNode"] = relationship(
        "FlowNode",
        primaryjoin="and_(FlowConnection.flow_id == FlowNode.flow_id, FlowConnection.source_node_id == FlowNode.node_id)",
        foreign_keys=[flow_id, source_node_id],
        back_populates="source_connections",
        overlaps="connections,flow,target_connections",
    )

    target_node: Mapped["FlowNode"] = relationship(
        "FlowNode",
        primaryjoin="and_(FlowConnection.flow_id == FlowNode.flow_id, FlowConnection.target_node_id == FlowNode.node_id)",
        foreign_keys=[flow_id, target_node_id],
        back_populates="target_connections",
        overlaps="connections,flow,source_connections,source_node",
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["flow_id", "source_node_id"],
            ["flow_nodes.flow_id", "flow_nodes.node_id"],
            name="fk_connection_source_node",
        ),
        ForeignKeyConstraint(
            ["flow_id", "target_node_id"],
            ["flow_nodes.flow_id", "flow_nodes.node_id"],
            name="fk_connection_target_node",
        ),
        UniqueConstraint(
            "flow_id",
            "source_node_id",
            "target_node_id",
            "connection_type",
            name="uq_flow_connection",
        ),
    )

    def __repr__(self) -> str:
        return f"<FlowConnection {self.source_node_id} -> {self.target_node_id} ({self.connection_type})>"


class ConversationSession(Base):
    __tablename__ = "conversation_sessions"  # type: ignore[assignment]

    __table_args__ = (
        Index(
            "ix_conversation_sessions_school_started",
            text("(state #>> '{context,school_wriveted_id}')"),
            "started_at",
            postgresql_where=text(
                "length(state #>> '{context,school_wriveted_id}') = 36"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
        primary_key=True,
    )

    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", name="fk_session_user", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    flow_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("flow_definitions.id", name="fk_session_flow", ondelete="CASCADE"),
        nullable=False,
    )

    session_token: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True, index=True
    )

    current_node_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Track which flow the current node belongs to (for sub-flow support)
    # Defaults to flow_id but changes when entering sub-flows
    current_flow_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey(
            "flow_definitions.id", name="fk_session_current_flow", ondelete="SET NULL"
        ),
        nullable=True,
    )

    state: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSONB),  # type: ignore[arg-type]
        nullable=False,
        server_default=text("'{}'::json"),
    )

    info: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSONB),  # type: ignore[arg-type]
        nullable=False,
        server_default=text("'{}'::json"),
    )

    started_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )

    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )

    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    status: Mapped[SessionStatus] = mapped_column(
        Enum(SessionStatus, name="enum_conversation_session_status"),
        nullable=False,
        server_default=text("'ACTIVE'"),
        index=True,
    )

    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )

    state_hash: Mapped[Optional[str]] = mapped_column(String(44), nullable=True)

    # Execution tracing fields
    trace_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    trace_level: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True, server_default=text("'standard'")
    )

    # Flow version at session start - for historical replay accuracy
    flow_version: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True, index=True
    )

    # Relationships
    user: Mapped[Optional["User"]] = relationship(
        "User", foreign_keys=[user_id], lazy="select"
    )

    flow: Mapped["FlowDefinition"] = relationship(
        "FlowDefinition", back_populates="sessions", foreign_keys=[flow_id]
    )

    history: Mapped[list["ConversationHistory"]] = relationship(
        "ConversationHistory", back_populates="session", cascade="all, delete-orphan"
    )

    execution_steps: Mapped[list["FlowExecutionStep"]] = relationship(
        "FlowExecutionStep", back_populates="session", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<ConversationSession {self.session_token} ({self.status})>"


class ConversationHistory(Base):
    __tablename__ = "conversation_history"  # type: ignore[assignment]

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
        primary_key=True,
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "conversation_sessions.id", name="fk_history_session", ondelete="CASCADE"
        ),
        nullable=False,
        index=True,
    )

    node_id: Mapped[str] = mapped_column(String(255), nullable=False)

    interaction_type: Mapped[InteractionType] = mapped_column(
        Enum(InteractionType, name="enum_interaction_type"), nullable=False
    )

    content: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSONB),
        nullable=False,  # type: ignore[arg-type]
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp(), index=True
    )

    # Relationships
    session: Mapped["ConversationSession"] = relationship(
        "ConversationSession", back_populates="history"
    )

    def __repr__(self) -> str:
        return f"<ConversationHistory {self.interaction_type} at {self.node_id}>"


class ConversationAnalytics(Base):
    __tablename__ = "conversation_analytics"  # type: ignore[assignment]

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
        primary_key=True,
    )

    flow_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("flow_definitions.id", name="fk_analytics_flow", ondelete="CASCADE"),
        nullable=False,
    )

    node_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    metrics: Mapped[Dict[str, Any]] = mapped_column(
        MutableDict.as_mutable(JSONB),
        nullable=False,  # type: ignore[arg-type]
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )

    # Relationships
    flow: Mapped["FlowDefinition"] = relationship(
        "FlowDefinition", back_populates="analytics"
    )

    __table_args__ = (
        UniqueConstraint(
            "flow_id", "node_id", "date", name="uq_analytics_flow_node_date"
        ),
    )

    def __repr__(self) -> str:
        return f"<ConversationAnalytics {self.flow_id} {self.node_id} {self.date}>"


class ChatTheme(Base):
    __tablename__ = "chat_themes"  # type: ignore[assignment]

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
        primary_key=True,
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)

    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    school_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey(
            "schools.wriveted_identifier", name="fk_theme_school", ondelete="CASCADE"
        ),
        nullable=True,
        index=True,
    )

    config: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSONB),
        nullable=False,  # type: ignore[arg-type]
    )

    logo_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    avatar_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), index=True
    )

    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    version: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default=text("'1.0'")
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", name="fk_theme_created_by", ondelete="SET NULL"),
        nullable=True,
    )

    created_by_user: Mapped[Optional["User"]] = relationship(
        "User", foreign_keys=[created_by], lazy="select"
    )

    def __repr__(self) -> str:
        return f"<ChatTheme {self.name} v{self.version}>"

    def __acl__(self) -> List[tuple[Any, str, str]]:
        """Defines who can do what to the theme"""
        policies = [
            (Allow, "role:admin", All),
            (Allow, "role:user", "read"),
        ]
        return policies


class IdempotencyRecord(Base):
    __tablename__ = "task_idempotency_records"  # type: ignore[assignment]

    idempotency_key: Mapped[str] = mapped_column(
        String(255), primary_key=True, index=True
    )

    status: Mapped[TaskExecutionStatus] = mapped_column(
        Enum(TaskExecutionStatus, name="enum_task_execution_status"),
        nullable=False,
        server_default=text("'PROCESSING'"),
        index=True,
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )

    node_id: Mapped[str] = mapped_column(String(255), nullable=False)

    session_revision: Mapped[int] = mapped_column(Integer, nullable=False)

    result_data: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        MutableDict.as_mutable(JSONB),
        nullable=True,
        server_default=text("NULL"),
    )

    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )

    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    expires_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=text("(CURRENT_TIMESTAMP + INTERVAL '24 hours')"),
    )

    def __repr__(self) -> str:
        return f"<IdempotencyRecord {self.idempotency_key} {self.status}>"


class TraceLevel(CaseInsensitiveStringEnum):
    """Trace detail level for session replay."""

    MINIMAL = "minimal"
    STANDARD = "standard"
    VERBOSE = "verbose"


class FlowExecutionStep(Base):
    """Captures detailed execution data for each node visit during a session.

    Used for session replay and debugging. State snapshots are PII-masked.
    """

    __tablename__ = "flow_execution_steps"  # type: ignore[assignment]

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
        primary_key=True,
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "conversation_sessions.id",
            name="fk_exec_step_session",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    node_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    node_type: Mapped[str] = mapped_column(String(50), nullable=False)

    step_number: Mapped[int] = mapped_column(Integer, nullable=False)

    # State snapshots (PII-masked)
    state_before: Mapped[Dict[str, Any]] = mapped_column(
        MutableDict.as_mutable(JSONB),
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )

    state_after: Mapped[Dict[str, Any]] = mapped_column(
        MutableDict.as_mutable(JSONB),
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )

    # Node-specific execution details (typed per node type)
    execution_details: Mapped[Dict[str, Any]] = mapped_column(
        MutableDict.as_mutable(JSONB),
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )

    # Connection taken to next node
    connection_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    next_node_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Timing
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Error tracking
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_details: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSONB, nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    session: Mapped["ConversationSession"] = relationship(
        "ConversationSession", back_populates="execution_steps"
    )

    __table_args__ = (
        Index(
            "ix_flowexecutionsteps_session_id_step_number", "session_id", "step_number"
        ),
        Index(
            "ix_flowexecutionsteps_session_id_started_at",
            "session_id",
            "started_at",
            postgresql_where=text("completed_at IS NOT NULL"),
        ),
        Index(
            "ix_flowexecutionsteps_session_id_node_id",
            "session_id",
            "node_id",
            postgresql_where=text("error_message IS NOT NULL"),
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<FlowExecutionStep {self.step_number}: {self.node_id} ({self.node_type})>"
        )


class TraceAccessAudit(Base):
    """Audit log for tracking access to sensitive trace data."""

    __tablename__ = "trace_access_audit"  # type: ignore[assignment]

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
        primary_key=True,
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "conversation_sessions.id",
            name="fk_trace_audit_session",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    accessed_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", name="fk_trace_audit_user", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    access_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # 'view_trace', 'export', 'list'

    accessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )

    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Which steps/fields were accessed
    data_accessed: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSONB, nullable=True
    )

    # Relationships
    session: Mapped["ConversationSession"] = relationship(
        "ConversationSession", foreign_keys=[session_id]
    )
    user: Mapped["User"] = relationship("User", foreign_keys=[accessed_by])

    def __repr__(self) -> str:
        return f"<TraceAccessAudit {self.access_type} by {self.accessed_by}>"

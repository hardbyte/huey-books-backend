from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from pydantic import UUID4, BaseModel, ConfigDict, Field, field_validator

from app.models.cms import (
    ConnectionType,
    ContentStatus,
    ContentType,
    ContentVisibility,
    InteractionType,
    NodeType,
    SessionStatus,
)
from app.schemas.pagination import PaginatedResponse
from app.schemas.recommendations import HueKeys

# Known input types for question nodes. Flow authors can define custom types
# without schema changes — unknown types fall back to text input on the frontend.
KNOWN_INPUT_TYPES: frozenset[str] = frozenset(
    {
        "text",
        "number",
        "email",
        "phone",
        "url",
        "date",
        "choice",
        "multiple_choice",
        "button",
        "slider",
        "image_choice",
        "carousel",
        "book_feedback",
        "continue",
    }
)


# Content Schemas
class ContentCreate(BaseModel):
    type: ContentType
    content: Dict[str, Any]
    info: Optional[Dict[str, Any]] = Field(default={})
    tags: Optional[List[str]] = []
    is_active: Optional[bool] = True
    status: Optional[ContentStatus] = ContentStatus.DRAFT
    school_id: Optional[UUID4] = None
    visibility: ContentVisibility = ContentVisibility.WRIVETED

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("content")
    @classmethod
    def validate_content_security(cls, v):
        if not v:
            raise ValueError("Content cannot be empty")

        # Import bleach for HTML sanitization
        import bleach

        # Convert content to string for processing if it's a dict
        if isinstance(v, dict):
            # For dict content, sanitize string values recursively
            return cls._sanitize_dict_content(v)

        content_str = str(v)

        # Allowed HTML tags and attributes for rich content
        allowed_tags = [
            "p",
            "br",
            "strong",
            "em",
            "u",
            "ol",
            "ul",
            "li",
            "a",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "blockquote",
            "code",
        ]
        allowed_attributes = {"a": ["href", "title"], "*": ["class", "id"]}

        # Clean the content using bleach
        cleaned_content = bleach.clean(
            content_str,
            tags=allowed_tags,
            attributes=allowed_attributes,
            strip=True,  # Remove disallowed tags entirely
        )

        # For basic validation, we should allow HTML entity encoding differences
        # Only fail if actual dangerous content was removed
        import html

        # Normalize both strings by decoding HTML entities for comparison
        normalized_original = html.unescape(content_str)
        normalized_cleaned = html.unescape(cleaned_content)

        # Check if content was significantly modified (actual XSS attempt)
        if normalized_cleaned != normalized_original:
            # Only raise error if the difference is substantial (not just entity encoding)
            if len(normalized_original) - len(normalized_cleaned) > 5:
                raise ValueError(
                    "Content contains potentially dangerous HTML that was sanitized"
                )

        return v

    @classmethod
    def _sanitize_dict_content(cls, content_dict):
        """Recursively sanitize string values in a dictionary."""
        import bleach

        sanitized = {}
        allowed_tags = [
            "p",
            "br",
            "strong",
            "em",
            "u",
            "ol",
            "ul",
            "li",
            "a",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "blockquote",
            "code",
        ]
        allowed_attributes = {"a": ["href", "title"], "*": ["class", "id"]}

        for key, value in content_dict.items():
            if isinstance(value, str):
                # Sanitize string values
                cleaned_value = bleach.clean(
                    value, tags=allowed_tags, attributes=allowed_attributes, strip=True
                )
                # Allow HTML entity encoding differences for legitimate characters
                import html

                normalized_original = html.unescape(value)
                normalized_cleaned = html.unescape(cleaned_value)

                if normalized_cleaned != normalized_original:
                    # Only raise error if the difference is substantial (not just entity encoding)
                    if len(normalized_original) - len(normalized_cleaned) > 5:
                        raise ValueError(
                            f"Content field '{key}' contains potentially dangerous HTML"
                        )
                sanitized[key] = value
            elif isinstance(value, dict):
                # Recursively sanitize nested dictionaries
                sanitized[key] = cls._sanitize_dict_content(value)
            elif isinstance(value, list):
                # Sanitize list items if they're strings
                sanitized_list = []
                for item in value:
                    if isinstance(item, str):
                        cleaned_item = bleach.clean(
                            item,
                            tags=allowed_tags,
                            attributes=allowed_attributes,
                            strip=True,
                        )
                        # Allow HTML entity encoding differences for legitimate characters
                        import html

                        normalized_original = html.unescape(item)
                        normalized_cleaned = html.unescape(cleaned_item)

                        if normalized_cleaned != normalized_original:
                            # Only raise error if the difference is substantial (not just entity encoding)
                            if len(normalized_original) - len(normalized_cleaned) > 5:
                                raise ValueError(
                                    f"Content list in '{key}' contains potentially dangerous HTML"
                                )
                        sanitized_list.append(item)
                    else:
                        sanitized_list.append(item)
                sanitized[key] = sanitized_list
            else:
                # Keep non-string values as-is
                sanitized[key] = value

        return sanitized


class ContentUpdate(BaseModel):
    type: Optional[ContentType] = None
    content: Optional[Dict[str, Any]] = None
    info: Optional[Dict[str, Any]] = Field(default=None)
    tags: Optional[List[str]] = None
    is_active: Optional[bool] = None
    status: Optional[ContentStatus] = None
    version: Optional[int] = None
    school_id: Optional[UUID4] = None
    visibility: Optional[ContentVisibility] = None

    model_config = ConfigDict(populate_by_name=True)


class ContentBrief(BaseModel):
    id: UUID4
    type: ContentType
    tags: List[str]
    is_active: bool
    status: ContentStatus
    version: int
    school_id: Optional[UUID4] = None
    visibility: ContentVisibility
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ContentDetail(ContentBrief):
    content: Dict[str, Any]
    info: Dict[str, Any]
    created_by: Optional[UUID4] = None

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class ContentResponse(PaginatedResponse):
    data: List[ContentDetail]


# Content Variant Schemas
class ContentVariantCreate(BaseModel):
    variant_key: str = Field(..., max_length=100)
    variant_data: Dict[str, Any]
    weight: Optional[int] = 100
    conditions: Optional[Dict[str, Any]] = {}
    is_active: Optional[bool] = True


class ContentVariantUpdate(BaseModel):
    variant_data: Optional[Dict[str, Any]] = None
    weight: Optional[int] = None
    conditions: Optional[Dict[str, Any]] = None
    performance_data: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None


class ContentVariantDetail(BaseModel):
    id: UUID4
    content_id: UUID4
    variant_key: str
    variant_data: Dict[str, Any]
    weight: int
    conditions: Dict[str, Any]
    performance_data: Dict[str, Any]
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ContentVariantResponse(PaginatedResponse):
    data: List[ContentVariantDetail]


class VariantPerformanceUpdate(BaseModel):
    impressions: Optional[int] = None
    engagements: Optional[int] = None
    conversions: Optional[int] = None


# Flow Schemas
class FlowEntryRequirements(BaseModel):
    variables: List[str] = Field(default_factory=list)
    description: Optional[str] = None


class FlowContract(BaseModel):
    entry_requirements: Optional[FlowEntryRequirements] = None
    return_state: List[str] = Field(default_factory=list)
    notes: Optional[str] = None


class FlowCreate(BaseModel):
    name: str = Field(
        ..., min_length=1, max_length=255, description="Flow name cannot be empty"
    )
    description: Optional[str] = None
    version: str = Field(..., max_length=50)
    flow_data: Dict[str, Any]
    entry_node_id: Optional[str] = Field(None, max_length=255)
    info: Optional[Dict[str, Any]] = Field(default={})
    contract: Optional[FlowContract] = None
    is_published: Optional[bool] = False
    is_active: Optional[bool] = True
    school_id: Optional[UUID4] = None
    visibility: ContentVisibility = ContentVisibility.WRIVETED

    model_config = ConfigDict(populate_by_name=True)


class FlowUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    version: Optional[str] = Field(None, max_length=50)
    flow_data: Optional[Dict[str, Any]] = None
    entry_node_id: Optional[str] = Field(None, max_length=255)
    info: Optional[Dict[str, Any]] = Field(default=None)
    contract: Optional[FlowContract] = None
    is_active: Optional[bool] = None
    publish: Optional[bool] = None
    school_id: Optional[UUID4] = None
    visibility: Optional[ContentVisibility] = None

    model_config = ConfigDict(populate_by_name=True)


class FlowBrief(BaseModel):
    id: UUID4
    name: str
    version: str
    is_published: bool
    is_active: bool
    school_id: Optional[UUID4] = None
    visibility: ContentVisibility
    created_at: datetime
    updated_at: datetime
    published_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class FlowDetail(FlowBrief):
    description: Optional[str] = None
    flow_data: Dict[str, Any]
    entry_node_id: Optional[str] = Field(None, max_length=255)
    info: Dict[str, Any] = Field()
    contract: Optional[FlowContract] = None
    created_by: Optional[UUID4] = None
    published_by: Optional[UUID4] = None

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class FlowResponse(PaginatedResponse):
    data: List[FlowDetail]


class FlowPublishRequest(BaseModel):
    publish: bool = True
    increment_version: Optional[bool] = False
    version_type: Optional[str] = Field("patch", pattern=r"^(major|minor|patch)$")


class FlowCloneRequest(BaseModel):
    name: str = Field(..., max_length=255)
    description: Optional[str] = None
    version: str = Field(..., max_length=50)
    clone_nodes: Optional[bool] = True
    clone_connections: Optional[bool] = True
    info: Optional[Dict[str, Any]] = Field(None)


# Flow Node Schemas
class NodeCreate(BaseModel):
    node_id: str = Field(..., max_length=255)
    node_type: NodeType
    template: Optional[str] = Field(None, max_length=100)
    content: Dict[str, Any]
    position: Optional[Dict[str, Any]] = {"x": 0, "y": 0}
    info: Optional[Dict[str, Any]] = {}


class NodeUpdate(BaseModel):
    node_type: Optional[NodeType] = None
    template: Optional[str] = Field(None, max_length=100)
    content: Optional[Dict[str, Any]] = None
    position: Optional[Dict[str, Any]] = None
    info: Optional[Dict[str, Any]] = None


class NodeDetail(BaseModel):
    id: UUID4
    flow_id: UUID4
    node_id: str
    node_type: NodeType
    template: Optional[str] = None
    content: Dict[str, Any]
    position: Dict[str, Any]
    info: Dict[str, Any]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class NodeResponse(PaginatedResponse):
    data: List[NodeDetail]


class NodePositionUpdate(BaseModel):
    positions: Dict[str, Dict[str, Any]]


# Flow Connection Schemas
class ConnectionCreate(BaseModel):
    source_node_id: str = Field(..., max_length=255)
    target_node_id: str = Field(..., max_length=255)
    connection_type: ConnectionType
    conditions: Optional[Dict[str, Any]] = {}
    info: Optional[Dict[str, Any]] = {}


class ConnectionDetail(BaseModel):
    id: UUID4
    flow_id: UUID4
    source_node_id: str
    target_node_id: str
    connection_type: ConnectionType
    conditions: Dict[str, Any]
    info: Dict[str, Any]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ConnectionResponse(PaginatedResponse):
    data: List[ConnectionDetail]


# Conversation Session Schemas
class SessionCreate(BaseModel):
    flow_id: UUID4
    user_id: Optional[UUID4] = None
    initial_state: Optional[Dict[str, Any]] = {}


class SessionDetail(BaseModel):
    session_id: UUID4 = Field(alias="id", serialization_alias="session_id")
    user_id: Optional[UUID4] = None
    flow_id: UUID4
    session_token: str
    current_node_id: Optional[str] = None
    state: Dict[str, Any]
    info: Dict[str, Any]
    started_at: datetime
    last_activity_at: datetime
    ended_at: Optional[datetime] = None
    status: SessionStatus
    revision: int
    state_hash: Optional[str] = None

    model_config = ConfigDict(
        from_attributes=True, populate_by_name=True, use_serialization_alias=True
    )


class SessionStartResponse(BaseModel):
    session_id: UUID4
    session_token: str
    csrf_token: Optional[str] = None  # Included for cross-origin scenarios
    next_node: Optional[Dict[str, Any]] = None
    theme_id: Optional[UUID4] = None
    theme: Optional[Dict[str, Any]] = None  # Full theme config if available
    flow_name: Optional[str] = None  # Flow name for display purposes


class SessionStateUpdate(BaseModel):
    updates: Dict[str, Any]
    expected_revision: Optional[int] = None


# Conversation Interaction Schemas
class InteractionCreate(BaseModel):
    input: str
    input_type: str = Field(
        ...,
        pattern=r"^[a-z][a-z0-9_]{0,49}$",
        json_schema_extra={"examples": sorted(KNOWN_INPUT_TYPES)},
    )


class InteractionResponse(BaseModel):
    messages: List[Dict[str, Any]]
    input_request: Optional[Dict[str, Any]] = None
    session_ended: bool = False
    current_node_id: Optional[str] = None
    session_updated: Optional[Dict[str, Any]] = None
    wait_for_acknowledgment: bool = False


class ConversationHistoryDetail(BaseModel):
    id: UUID4
    session_id: UUID4
    node_id: str
    interaction_type: InteractionType
    content: Dict[str, Any]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ConversationHistoryResponse(PaginatedResponse):
    data: List[ConversationHistoryDetail]


# Analytics Schemas
class AnalyticsGranularity(str, Enum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


class AnalyticsDetail(BaseModel):
    id: UUID4
    flow_id: UUID4
    node_id: Optional[str] = None
    date: date
    metrics: Dict[str, Any]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AnalyticsResponse(PaginatedResponse):
    data: List[AnalyticsDetail]


class FunnelAnalyticsRequest(BaseModel):
    start_node: str
    end_node: str


class FunnelAnalyticsResponse(BaseModel):
    funnel_steps: List[Dict[str, Any]]
    conversion_rate: float
    total_sessions: int


class AnalyticsExportRequest(BaseModel):
    flow_id: UUID4
    format: str = Field(..., pattern="^(csv|json)$")


# Bulk Operations Schemas
class BulkOperation(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class BulkContentRequest(BaseModel):
    operation: BulkOperation
    items: List[Union[ContentCreate, ContentUpdate, UUID4]]


class BulkContentResponse(BaseModel):
    success_count: int
    error_count: int
    errors: List[Dict[str, Any]] = []


class BulkContentUpdateRequest(BaseModel):
    content_ids: List[UUID4]
    updates: Dict[str, Any]


class BulkContentUpdateResponse(BaseModel):
    updated_count: int
    errors: List[Dict[str, Any]] = []


class BulkContentDeleteRequest(BaseModel):
    content_ids: List[UUID4]


class BulkContentDeleteResponse(BaseModel):
    deleted_count: int
    errors: List[Dict[str, Any]] = []


# Content Workflow Schemas
class ContentStatusUpdate(BaseModel):
    status: ContentStatus
    comment: Optional[str] = None


# Webhook Schemas
class WebhookCreate(BaseModel):
    url: str = Field(..., pattern="^https?://.*")
    events: List[str]
    headers: Optional[Dict[str, str]] = {}
    is_active: Optional[bool] = True


class WebhookUpdate(BaseModel):
    url: Optional[str] = Field(None, pattern="^https?://.*")
    events: Optional[List[str]] = None
    headers: Optional[Dict[str, str]] = None
    is_active: Optional[bool] = None


class WebhookDetail(BaseModel):
    id: UUID4
    url: str
    events: List[str]
    headers: Dict[str, str]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class WebhookResponse(PaginatedResponse):
    data: List[WebhookDetail]


class WebhookTestResponse(BaseModel):
    success: bool
    status_code: Optional[int] = None
    response_time: Optional[float] = None
    error: Optional[str] = None


# Content Type Specific Schemas
class JokeAnswer(BaseModel):
    text: str
    value: str
    punchline: Optional[str] = None
    reward_url: Optional[str] = None


class JokeContent(BaseModel):
    question_text: str
    answers: List[JokeAnswer]
    min_age: Optional[int] = None
    max_age: Optional[int] = None


class FactContent(BaseModel):
    text: str
    source: Optional[str] = None
    topic: Optional[str] = None
    difficulty: Optional[str] = None


class MessageContent(BaseModel):
    text: str
    rich_text: Optional[str] = None
    typing_delay: Optional[float] = None
    media: Optional[Dict[str, Any]] = None


class QuestionContent(BaseModel):
    text: str
    options: Optional[List[Dict[str, str]]] = []
    input_type: Optional[str] = "text"


class PreferenceAnswer(BaseModel):
    """A single answer option for a preference/personality question.

    Each answer maps to hue dimensions with float weights indicating how
    strongly selecting this answer correlates with each reading preference.
    """

    text: str = Field(..., min_length=1, description="Display text for the answer")
    image_url: Optional[str] = Field(
        None, max_length=512, description="Optional image URL"
    )
    hue_map: Dict[HueKeys, float] = Field(
        ...,
        description="Mapping of hue dimensions to weights (0.0-1.0)",
    )

    @field_validator("hue_map")
    @classmethod
    def validate_hue_weights(cls, v: Dict[HueKeys, float]) -> Dict[HueKeys, float]:
        """Ensure all hue weights are within valid range."""
        for hue_key, weight in v.items():
            if not 0.0 <= weight <= 1.0:
                raise ValueError(
                    f"Hue weight for {hue_key} must be between 0.0 and 1.0, got {weight}"
                )
        return v


class PreferenceQuestionContent(BaseModel):
    """Content schema for preference/personality questions used in chatbot flows.

    These questions help determine a user's reading preferences by mapping
    their answers to the 13 Huey hue dimensions.
    """

    question_text: str = Field(..., min_length=1, description="The question to display")
    min_age: int = Field(0, ge=0, le=99, description="Minimum recommended age")
    max_age: int = Field(99, ge=0, le=99, description="Maximum recommended age")
    answers: List[PreferenceAnswer] = Field(
        ...,
        min_length=2,
        max_length=6,
        description="Answer options (2-6 choices)",
    )

    @field_validator("answers")
    @classmethod
    def validate_answers(cls, v: List[PreferenceAnswer]) -> List[PreferenceAnswer]:
        """Ensure answers have unique text values."""
        texts = [a.text for a in v]
        if len(texts) != len(set(texts)):
            raise ValueError("Answer text values must be unique within a question")
        return v


class QuoteContent(BaseModel):
    text: str
    author: Optional[str] = None
    source: Optional[str] = None


class PromptContent(BaseModel):
    text: str
    context: Optional[str] = None
    expected_response_type: Optional[str] = None


# Node Content Type Schemas
class MessageNodeContent(BaseModel):
    messages: List[Dict[str, Any]]
    typing_indicator: Optional[bool] = True


class QuestionNodeContent(BaseModel):
    """Question node content schema with support for rich input types.

    Input types:
    - text: Free text input
    - number: Numeric input with optional min/max
    - email: Email address input
    - phone: Phone number input
    - url: URL input
    - date: Date picker
    - choice: Single selection from options (buttons/radio)
    - multiple_choice: Multiple selection from options (checkboxes)
    - slider: Range slider (for age, ratings, scales)
    - image_choice: Single selection from image-based options
    - carousel: Swipeable carousel for browsing items (e.g., books)
    """

    question: Dict[str, Any]
    input_type: str  # Validated by node_input_validation.py
    options: Optional[
        List[Dict[str, Any]]
    ] = []  # Options can have image_url, label, etc.
    validation: Optional[Dict[str, Any]] = {}
    slider_config: Optional[Dict[str, Any]] = None  # min, max, step, labels
    carousel_config: Optional[Dict[str, Any]] = None  # items_per_view, show_navigation
    variable: Optional[str] = None  # Where to store the response


class ConditionNodeContent(BaseModel):
    conditions: List[Dict[str, Any]]


class ActionNodeContent(BaseModel):
    action: str
    params: Dict[str, Any]


class WebhookNodeContent(BaseModel):
    url: str
    method: str = "POST"
    headers: Optional[Dict[str, str]] = {}
    payload: Optional[Dict[str, Any]] = {}


# Chat Theme Schemas
class ChatThemeColors(BaseModel):
    primary: str = Field(default="#1890ff", pattern=r"^#[0-9a-fA-F]{6}$")
    secondary: str = Field(default="#52c41a", pattern=r"^#[0-9a-fA-F]{6}$")
    background: str = Field(default="#ffffff", pattern=r"^#[0-9a-fA-F]{6}$")
    backgroundAlt: str = Field(default="#f5f5f5", pattern=r"^#[0-9a-fA-F]{6}$")
    header: str = Field(default="#f5f5f5", pattern=r"^#[0-9a-fA-F]{6}$")
    headerText: str = Field(default="#262626", pattern=r"^#[0-9a-fA-F]{6}$")
    userBubble: str = Field(default="#e6f7ff", pattern=r"^#[0-9a-fA-F]{6}$")
    userBubbleText: str = Field(default="#000000", pattern=r"^#[0-9a-fA-F]{6}$")
    botBubble: str = Field(default="#f0f0f0", pattern=r"^#[0-9a-fA-F]{6}$")
    botBubbleText: str = Field(default="#262626", pattern=r"^#[0-9a-fA-F]{6}$")
    border: str = Field(default="#d9d9d9", pattern=r"^#[0-9a-fA-F]{6}$")
    shadow: str = Field(default="rgba(0,0,0,0.1)")
    error: str = Field(default="#ff4d4f", pattern=r"^#[0-9a-fA-F]{6}$")
    success: str = Field(default="#52c41a", pattern=r"^#[0-9a-fA-F]{6}$")
    warning: str = Field(default="#faad14", pattern=r"^#[0-9a-fA-F]{6}$")
    text: str = Field(default="#262626", pattern=r"^#[0-9a-fA-F]{6}$")
    textMuted: str = Field(default="#8c8c8c", pattern=r"^#[0-9a-fA-F]{6}$")
    link: str = Field(default="#1890ff", pattern=r"^#[0-9a-fA-F]{6}$")


class ChatThemeFontSize(BaseModel):
    small: str = Field(default="12px")
    medium: str = Field(default="14px")
    large: str = Field(default="16px")


class ChatThemeFontWeight(BaseModel):
    normal: int = Field(default=400)
    medium: int = Field(default=500)
    bold: int = Field(default=600)


class ChatThemeTypography(BaseModel):
    fontFamily: str = Field(
        default="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    )
    fontSize: ChatThemeFontSize = Field(default_factory=ChatThemeFontSize)
    lineHeight: float = Field(default=1.5)
    fontWeight: ChatThemeFontWeight = Field(default_factory=ChatThemeFontWeight)


class ChatThemeBubbles(BaseModel):
    borderRadius: int = Field(default=12)
    padding: str = Field(default="12px 16px")
    maxWidth: str = Field(default="80%")
    spacing: int = Field(default=8)


class ChatThemeBot(BaseModel):
    name: str = Field(default="Huey")
    avatar: str = Field(default="")
    typingIndicator: str = Field(default="dots", pattern=r"^(dots|text|wave|none)$")
    typingSpeed: int = Field(default=50)
    responseDelay: int = Field(default=500)


class ChatThemeLayout(BaseModel):
    position: str = Field(
        default="bottom-right",
        pattern=r"^(bottom-right|bottom-left|bottom-center|fullscreen|inline)$",
    )
    width: Union[int, str] = Field(default=400)
    height: Union[int, str] = Field(default=600)
    maxWidth: str = Field(default="90vw")
    maxHeight: str = Field(default="90vh")
    margin: str = Field(default="20px")
    padding: str = Field(default="16px")
    showHeader: bool = Field(default=True)
    showFooter: bool = Field(default=True)
    headerHeight: int = Field(default=60)
    footerHeight: int = Field(default=80)


class ChatThemeAnimations(BaseModel):
    enabled: bool = Field(default=True)
    messageEntry: str = Field(default="fade", pattern=r"^(fade|slide|none)$")
    duration: int = Field(default=300)
    easing: str = Field(default="ease-in-out")


class ChatThemeAccessibility(BaseModel):
    highContrast: bool = Field(default=False)
    reduceMotion: bool = Field(default=False)
    fontSize: str = Field(default="default", pattern=r"^(default|large|xlarge)$")


class ChatThemeConfig(BaseModel):
    colors: ChatThemeColors = Field(default_factory=ChatThemeColors)
    typography: ChatThemeTypography = Field(default_factory=ChatThemeTypography)
    bubbles: ChatThemeBubbles = Field(default_factory=ChatThemeBubbles)
    bot: ChatThemeBot = Field(default_factory=ChatThemeBot)
    layout: ChatThemeLayout = Field(default_factory=ChatThemeLayout)
    animations: ChatThemeAnimations = Field(default_factory=ChatThemeAnimations)
    accessibility: ChatThemeAccessibility = Field(
        default_factory=ChatThemeAccessibility
    )
    customCSS: Optional[str] = None


class ChatThemeCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    school_id: Optional[UUID4] = None
    config: ChatThemeConfig
    logo_url: Optional[str] = Field(None, max_length=512)
    avatar_url: Optional[str] = Field(None, max_length=512)
    is_active: bool = Field(default=True)
    is_default: bool = Field(default=False)
    version: str = Field(default="1.0", max_length=50)

    model_config = ConfigDict(populate_by_name=True)


class ChatThemeUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    config: Optional[ChatThemeConfig] = None
    logo_url: Optional[str] = Field(None, max_length=512)
    avatar_url: Optional[str] = Field(None, max_length=512)
    is_active: Optional[bool] = None
    is_default: Optional[bool] = None
    version: Optional[str] = Field(None, max_length=50)

    model_config = ConfigDict(populate_by_name=True)


class ChatThemeDetail(BaseModel):
    id: UUID4
    name: str
    description: Optional[str] = None
    school_id: Optional[UUID4] = None
    config: Dict[str, Any]
    logo_url: Optional[str] = None
    avatar_url: Optional[str] = None
    is_active: bool
    is_default: bool
    version: str
    created_at: datetime
    updated_at: datetime
    created_by: Optional[UUID4] = None

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class ChatThemeResponse(PaginatedResponse):
    data: List[ChatThemeDetail]


# CEL Evaluation Schemas for test flow modal
class CELEvaluationRequest(BaseModel):
    """Request to evaluate a CEL expression with provided context."""

    expression: str = Field(..., description="CEL expression to evaluate")
    context: Dict[str, Any] = Field(
        default={}, description="Variables available during evaluation"
    )

    model_config = ConfigDict(populate_by_name=True)


class CELEvaluationResponse(BaseModel):
    """Response from CEL evaluation."""

    expression: str
    result: Any
    success: bool
    error: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)

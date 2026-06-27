import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, List, Optional

from fastapi_permissions import All, Allow, Authenticated
from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.cms import ContentVisibility
from app.schemas import CaseInsensitiveStringEnum

if TYPE_CHECKING:
    from app.models.booklist import BookList
    from app.models.cms import ChatTheme, FlowDefinition


class CampaignBiasMode(CaseInsensitiveStringEnum):
    """How a campaign's booklist influences recommendations.

    - BOOST: themed books rank higher while the full catalogue stays available.
    - FILTER: restrict recommendations to the booklist. Not yet honoured by the
      recommendation query, which treats every booklist as a BOOST.
    """

    BOOST = "boost"
    FILTER = "filter"


class Campaign(Base):
    """A targeting rule + payload bundle that resolves a chat experience.

    Resolves the right flow / visual theme / book bias for a session based on the
    reader's school, region and the current date. See
    docs/design-chatflow-segments.md.
    """

    __tablename__ = "campaigns"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
        primary_key=True,
        nullable=False,
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    slug: Mapped[Optional[str]] = mapped_column(
        String(200), nullable=True, index=True, unique=True
    )

    # --- Payload (all optional) ---
    flow_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("flow_definitions.id", name="fk_campaign_flow", ondelete="SET NULL"),
        nullable=True,
    )
    theme_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("chat_themes.id", name="fk_campaign_theme", ondelete="SET NULL"),
        nullable=True,
    )
    booklist_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("book_lists.id", name="fk_campaign_booklist", ondelete="SET NULL"),
        nullable=True,
    )
    bias_mode: Mapped[CampaignBiasMode] = mapped_column(
        Enum(
            CampaignBiasMode,
            name="enum_campaign_bias_mode",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        default=CampaignBiasMode.BOOST,
        server_default=CampaignBiasMode.BOOST.value,
    )

    # --- Targeting: structured (SQL-prefilterable) ---
    country_codes: Mapped[Optional[list[str]]] = mapped_column(
        ARRAY(String), nullable=True
    )
    region_states: Mapped[Optional[list[str]]] = mapped_column(
        ARRAY(String), nullable=True
    )
    school_ids: Mapped[Optional[list[int]]] = mapped_column(
        ARRAY(Integer), nullable=True
    )
    min_age: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_age: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # --- Targeting: optional CEL escape hatch (AND-gated after the structured prefilter) ---
    targeting_cel: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # --- Seasonality ---
    active_from: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    active_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # --- Precedence & lifecycle ---
    priority: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    info: Mapped[Optional[dict]] = mapped_column(MutableDict.as_mutable(JSONB))

    # --- Ownership / access / sharing ---
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", name="fk_campaign_created_by", ondelete="SET NULL"),
        nullable=True,
    )
    school_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("schools.id", name="fk_campaign_school", ondelete="CASCADE"),
        nullable=True,
    )
    visibility: Mapped[ContentVisibility] = mapped_column(
        Enum(
            ContentVisibility,
            name="enum_cms_content_visibility",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        default=ContentVisibility.WRIVETED,
        server_default=text("'wriveted'"),
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.current_timestamp(),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    flow: Mapped[Optional["FlowDefinition"]] = relationship(
        "FlowDefinition", foreign_keys=[flow_id]
    )
    theme: Mapped[Optional["ChatTheme"]] = relationship(
        "ChatTheme", foreign_keys=[theme_id]
    )
    booklist: Mapped[Optional["BookList"]] = relationship(
        "BookList", foreign_keys=[booklist_id]
    )

    def __repr__(self):
        return f"<Campaign '{self.name}' id={self.id} visibility={self.visibility}>"

    def __acl__(self) -> List[tuple[Any, str, str]]:
        """Who can do what to this Campaign (mirrors BookList's school-scoped ACL)."""
        policies = [
            (Allow, "role:admin", All),
            (Allow, f"user:{self.created_by}", All),
            (Allow, f"schooladmin:{self.school_id}", All),
            (Allow, f"educator:{self.school_id}", All),
        ]
        if self.school_id is not None:
            policies.append((Allow, f"student:{self.school_id}", "read"))
        if self.visibility in (ContentVisibility.PUBLIC, ContentVisibility.WRIVETED):
            policies.append((Allow, Authenticated, "read"))
        return policies

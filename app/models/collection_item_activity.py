import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, List

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.schemas import CaseInsensitiveStringEnum

if TYPE_CHECKING:
    from app.models.collection_item import CollectionItem
    from app.models.reader import Reader


class CollectionItemReadStatus(CaseInsensitiveStringEnum):
    UNREAD = "UNREAD"
    TO_READ = "TO_READ"
    NOT_INTERESTED = "NOT_INTERESTED"
    READING = "READING"
    STOPPED_READING = "STOPPED_READING"
    READ = "READ"


class CollectionItemActivity(Base):
    __tablename__ = "collection_item_activity_log"  # type: ignore[assignment]

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, nullable=False, autoincrement=True
    )

    collection_item_id: Mapped[int] = mapped_column(
        ForeignKey(
            "collection_items.id",
            name="fk_collection_item_activity_collection_item_id",
            ondelete="CASCADE",
        ),
        index=True,
        nullable=False,
    )

    collection_item: Mapped["CollectionItem"] = relationship(
        "CollectionItem", lazy="joined", passive_deletes=True
    )

    reader_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("readers.id", name="fk_collection_item_activity_reader"),
        nullable=False,
    )
    reader: Mapped["Reader"] = relationship(
        "Reader",
        back_populates="collection_item_activity_log",
        foreign_keys=[reader_id],
    )

    status: Mapped[CollectionItemReadStatus] = mapped_column(
        Enum(CollectionItemReadStatus, name="enum_collection_item_read_status"),
        default=CollectionItemReadStatus.UNREAD,
        nullable=False,
        index=True,
    )

    timestamp: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )

    Index(
        "ix_collection_activity_latest",
        collection_item_id,
        reader_id,
        timestamp.desc(),
        id.desc(),
    )

    # index the timestamp and reader_id together to allow for fast "current status" lookups
    Index(
        "ix_collectionitemactivitys_timestamp_reader_id",
        timestamp,
        reader_id,
    )

    def __repr__(self) -> str:
        return f"<CollectionItemActivity '{self.reader.name}' marked the book '{self.collection_item.get_display_title()}' as '{self.status}'>"

    def __acl__(self) -> List[tuple[Any, str, Any]]:
        return self.collection_item.__acl__()

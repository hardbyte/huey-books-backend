from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from sqlalchemy import (
    Boolean,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.associationproxy import association_proxy
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, column_property, mapped_column, relationship

from app.db import Base
from app.db.common_types import intpk
from app.models.collection_item import CollectionItem
from app.models.illustrator_edition_association import (
    illustrator_edition_association_table,
)

if TYPE_CHECKING:
    from app.models.collection import Collection
    from app.models.illustrator import Illustrator
    from app.models.work import Work


class Edition(Base):
    __tablename__ = "editions"  # type: ignore[assignment]
    __table_args__ = (
        Index("ix_editions_isbn_work_id", "isbn", postgresql_include=["work_id"]),
    )

    id: Mapped[intpk] = mapped_column(Integer, primary_key=True, autoincrement=True)

    isbn: Mapped[str] = mapped_column(
        String(200), nullable=False, index=True, unique=True
    )

    work_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("works.id", name="fk_editions_works"),
        index=True,
        nullable=True,
    )
    work: Mapped[Optional["Work"]] = relationship(
        "Work", back_populates="editions", lazy="joined"
    )

    # this might be a localized title
    edition_title: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    edition_subtitle: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    leading_article: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    # TODO computed columns for display_title / sort_title based on the above

    # "computed" column for edition_title coalesced with work title
    title: Mapped[Optional[str]] = mapped_column(
        String(512),
        index=True,
        nullable=True,
    )

    date_published: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    cover_url: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    # Info contains stuff like edition number, language
    # media (paperback/hardback/audiobook), number of pages.
    info: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        MutableDict.as_mutable(JSONB)
    )  # type: ignore[arg-type]

    # Proxy the authors from the related work
    authors = association_proxy("work", "authors")

    hydrated_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    hydrated: Mapped[bool] = mapped_column(
        Boolean,
        Computed("hydrated_at is not null"),
        index=True,
    )

    illustrators: Mapped[List["Illustrator"]] = relationship(
        "Illustrator",
        secondary=illustrator_edition_association_table,
        back_populates="editions",
        lazy="subquery",
    )

    collections: Mapped[List["Collection"]] = relationship(
        "Collection",
        secondary=CollectionItem.__table__,
        lazy="selectin",
        viewonly=True,
    )
    collection_count: Mapped[int] = column_property(
        select(func.count(CollectionItem.id))
        .where(CollectionItem.edition_isbn == isbn)
        .correlate_except(CollectionItem)
        .scalar_subquery()
    )

    def get_display_title(self) -> str:
        display_title = (
            f"{self.leading_article} {self.title}"
            if self.leading_article is not None
            else self.title
        )
        if not display_title and self.work:
            display_title = self.work.get_display_title()

        if not display_title:
            display_title = self.isbn

        return display_title

    # ---------these are used for the hybrid attribute used in querying by number of collections in GET:editions/to_hydrate---------
    # https://docs.sqlalchemy.org/en/14/orm/extensions/hybrid.html#defining-expression-behavior-distinct-from-attribute-behavior

    # this method and its equivalent expression need the same method name to work
    @hybrid_property
    def num_collections(self) -> int:
        return self.collections.count()

    @num_collections.expression  # type: ignore[no-redef]
    def num_collections(cls) -> Any:
        return (
            select(
                [
                    func.count(CollectionItem.__table__.c.edition_isbn).label(
                        "num_collections"
                    )
                ]
            )
            .where(CollectionItem.__table__.c.edition_isbn == cls.isbn)
            .label("total_collections")
        )

    # -------------------------------------------------------------------------------------------------------------------------

    def __repr__(self) -> str:
        return f"<Edition '{self.isbn}', {self.get_display_title()}>"

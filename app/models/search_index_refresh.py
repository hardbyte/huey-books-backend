from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class SearchIndexRefresh(Base):
    __tablename__ = "search_index_refreshes"

    index_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    refreshed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

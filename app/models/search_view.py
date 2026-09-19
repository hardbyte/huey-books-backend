from sqlalchemy import Column, Index, Integer, Table
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR

from app.db.base_class import Base

search_view_v1 = Table(
    "search_view_v1",
    Base.metadata,
    Column("work_id", Integer, primary_key=True),
    Column("author_ids", JSONB),
    Column("series_id", Integer),
    Column("document", TSVECTOR),
    Index(
        "uix_search_view_work_series",
        "work_id",
        "series_id",
        unique=True,
        postgresql_nulls_not_distinct=True,
    ),
)

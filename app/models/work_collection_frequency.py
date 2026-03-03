from sqlalchemy import BigInteger, Column, Integer, Table

from app.db.base_class import Base

work_collection_frequency = Table(
    "work_collection_frequency",
    Base.metadata,
    Column("work_id", Integer, primary_key=True),
    Column("collection_frequency", BigInteger),
    Column("school_count", BigInteger),
)

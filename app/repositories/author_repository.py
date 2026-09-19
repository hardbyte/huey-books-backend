"""
Author Repository - Domain-focused data access for authors.

Migrated from app.crud.author to follow repository pattern.
"""

from abc import ABC, abstractmethod
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import NoResultFound
from sqlalchemy.orm import Session

from app.models import Author
from app.schemas.author import AuthorCreateIn


def first_last_to_name_key(first_name: str, last_name: str) -> str:
    """Generate normalized name key for author lookup."""
    return "".join(
        char for char in f"{first_name or ''}{last_name}".lower() if char.isalnum()
    )


class AuthorRepository(ABC):
    """Repository interface for Author operations."""

    @abstractmethod
    def get_by_id(self, db: Session, author_id: int) -> Optional[Author]:
        """Get author by ID."""
        pass

    @abstractmethod
    def get_or_404(self, db: Session, id: int) -> Author:
        """Get author by ID or raise 404."""
        pass

    @abstractmethod
    def get_or_create(
        self, db: Session, author_data: AuthorCreateIn, commit: bool = True
    ) -> Author:
        """Get existing author or create new one."""
        pass

    @abstractmethod
    def create(
        self, db: Session, obj_in: AuthorCreateIn, commit: bool = True
    ) -> Author:
        """Create new author."""
        pass

    @abstractmethod
    def create_in_bulk(
        self, db: Session, bulk_author_data_in: List[AuthorCreateIn]
    ) -> None:
        """Bulk upsert authors."""
        pass

    @abstractmethod
    def search(
        self,
        db: Session,
        query_string: Optional[str] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> List[Author]:
        """Search authors with optional filters."""
        pass

    @abstractmethod
    def remove(self, db: Session, id: int) -> Author:
        """Delete author by ID."""
        pass


class AuthorRepositoryImpl(AuthorRepository):
    """SQLAlchemy implementation of AuthorRepository."""

    def get_by_id(self, db: Session, author_id: int) -> Optional[Author]:
        """Get author by ID."""
        return db.get(Author, author_id)

    def get_or_404(self, db: Session, id: int) -> Author:
        """Get author by ID or raise HTTPException 404."""
        author = self.get_by_id(db, id)
        if author is None:
            raise HTTPException(
                status_code=404,
                detail=f"Author with id {id} not found.",
            )
        return author

    def get_or_create(
        self, db: Session, author_data: AuthorCreateIn, commit: bool = True
    ) -> Author:
        """
        Get existing author by name key or create new one.

        Uses name_key (normalized first+last name) for lookup to avoid duplicates.
        """
        q = select(Author).where(
            Author.name_key
            == first_last_to_name_key(author_data.first_name, author_data.last_name)
        )
        try:
            author = db.execute(q).scalar_one()
        except NoResultFound:
            author = self.create(db, obj_in=author_data, commit=commit)

        return author

    def create(
        self, db: Session, obj_in: AuthorCreateIn, commit: bool = True
    ) -> Author:
        """Create new author."""
        orm_obj = Author(
            first_name=obj_in.first_name,
            last_name=obj_in.last_name,
            info=obj_in.info or {},
        )
        db.add(orm_obj)
        if commit:
            db.commit()
            db.refresh(orm_obj)
        else:
            db.flush()
        return orm_obj

    def create_in_bulk(
        self, db: Session, bulk_author_data_in: List[AuthorCreateIn]
    ) -> None:
        """
        Bulk upsert authors using PostgreSQL INSERT ON CONFLICT.

        Note: Relies on assumption that author names are unique (which may not be accurate).
        Uses on_conflict_do_nothing to skip duplicates silently.
        """
        insert_stmt = pg_insert(Author).on_conflict_do_nothing()

        values = [
            {
                "first_name": author.first_name,
                "last_name": author.last_name,
                "info": {} if author.info is None else author.info,
            }
            for author in bulk_author_data_in
        ]
        db.execute(insert_stmt, values)
        db.flush()

    def search(
        self,
        db: Session,
        query_string: Optional[str] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> List[Author]:
        """
        Search authors with optional name filter.

        Args:
            db: Database session
            query_string: Optional search string to filter by name
            skip: Number of records to skip (pagination)
            limit: Maximum number of records to return

        Returns:
            List of matching authors
        """
        query = select(Author)

        if query_string is not None:
            query = query.where(
                func.lower(Author.name_key).contains(query_string.lower())
            )

        query = query.order_by(Author.id).offset(skip).limit(limit)
        return list(db.scalars(query).all())

    def remove(self, db: Session, id: int) -> Author:
        """Delete author by ID."""
        author = self.get_by_id(db, id)
        if author is not None:
            db.delete(author)
            db.commit()
        return author


# Create singleton instance for backward compatibility
author_repository = AuthorRepositoryImpl()

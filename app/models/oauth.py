"""OAuth authorization-server persistence: consent grants, one-time
authorization codes, and rotating refresh tokens (with reuse detection).

Secrets are never stored in the clear: authorization codes and refresh tokens
are persisted only as SHA-256 hashes, so a database leak yields no usable
credential. A grant fixes the school and scopes at consent time; every token
minted from it inherits that per-school authority.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class OAuthGrant(Base):
    __tablename__ = "oauth_grants"  # type: ignore[assignment]

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_oauth_grant_user", ondelete="CASCADE"),
        nullable=False,
    )
    # The school this grant authorises (schools.wriveted_identifier).
    school_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    client_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # Space-separated consented scopes.
    scopes: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=text("now()"), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    authorization_codes: Mapped[list["OAuthAuthorizationCode"]] = relationship(
        back_populates="grant", cascade="all, delete-orphan"
    )
    refresh_tokens: Mapped[list["OAuthRefreshToken"]] = relationship(
        back_populates="grant", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_oauth_grants_user_id", "user_id"),
        Index("ix_oauth_grants_school_id", "school_id"),
    )


class OAuthAuthorizationCode(Base):
    __tablename__ = "oauth_authorization_codes"  # type: ignore[assignment]

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    grant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("oauth_grants.id", name="fk_oauth_code_grant", ondelete="CASCADE"),
        nullable=False,
    )
    redirect_uri: Mapped[str] = mapped_column(String(2048), nullable=False)
    code_challenge: Mapped[str] = mapped_column(String(255), nullable=False)
    code_challenge_method: Mapped[str] = mapped_column(
        String(10), nullable=False, default="S256"
    )
    scopes: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=text("now()"), nullable=False
    )

    grant: Mapped["OAuthGrant"] = relationship(back_populates="authorization_codes")


class OAuthRefreshToken(Base):
    __tablename__ = "oauth_refresh_tokens"  # type: ignore[assignment]

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    grant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "oauth_grants.id", name="fk_oauth_refresh_grant", ondelete="CASCADE"
        ),
        nullable=False,
    )
    # All rotations of one login share a family; reuse of a consumed token
    # revokes the whole family (token-theft detection).
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    scopes: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=text("now()"), nullable=False
    )

    grant: Mapped["OAuthGrant"] = relationship(back_populates="refresh_tokens")

    __table_args__ = (Index("ix_oauth_refresh_family", "family_id"),)

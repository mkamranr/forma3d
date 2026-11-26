from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, String, Uuid, false
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, CreatedAt, Timestamps, UTCDateTime, UUIDPk


class User(UUIDPk, Timestamps, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: May open the Configuration page (app settings, user management).
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)


class RefreshToken(UUIDPk, CreatedAt, Base):
    """Opaque refresh tokens, stored hashed. A family is one login session;
    presenting an already-rotated token revokes the whole family (reuse detection)."""

    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    family_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    rotated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

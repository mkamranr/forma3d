from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, Timestamps, UUIDPk

PROJECT_STATUSES = ("active", "archived", "deleted")


class Project(UUIDPk, Timestamps, Base):
    __tablename__ = "projects"
    __table_args__ = (Index("ix_projects_user_status", "user_id", "status"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    thumbnail_path: Mapped[str | None] = mapped_column(String(512))
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey(
            "scene_versions.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_projects_current_version_id_scene_versions",
        ),
    )
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False, index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

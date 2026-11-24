from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, CreatedAt, JSONType, Timestamps, UUIDPk


class SceneVersion(UUIDPk, CreatedAt, Base):
    """Immutable once status == 'committed'. Paths are relative to DATA_ROOT."""

    __tablename__ = "scene_versions"
    __table_args__ = (
        UniqueConstraint("project_id", "version_number", name="uq_scene_versions_project_number"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("scene_versions.id", ondelete="SET NULL")
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    blend_file_path: Mapped[str | None] = mapped_column(String(512))
    glb_file_path: Mapped[str | None] = mapped_column(String(512))
    thumbnail_path: Mapped[str | None] = mapped_column(String(512))
    operation_batch_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    message_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    object_inventory: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list, nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="committed", nullable=False, index=True)


class SceneObject(UUIDPk, Timestamps, Base):
    """Head-version inventory of application objects for a project.
    `blender_object_id` holds the stable application object id (e.g. `desk_top`)."""

    __tablename__ = "scene_objects"
    __table_args__ = (
        UniqueConstraint("project_id", "blender_object_id", name="uq_scene_objects_project_obj"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    blender_object_id: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_object_id: Mapped[str | None] = mapped_column(String(64))
    object_name: Mapped[str] = mapped_column(String(120), nullable=False)
    object_type: Mapped[str] = mapped_column(String(32), nullable=False)
    component_type: Mapped[str | None] = mapped_column(String(64))
    properties: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

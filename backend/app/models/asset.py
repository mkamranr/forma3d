from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, CreatedAt, JSONType, UUIDPk


class Asset(UUIDPk, CreatedAt, Base):
    __tablename__ = "assets"

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("scene_versions.id", ondelete="CASCADE"), index=True
    )
    asset_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # Relative to DATA_ROOT; never exposed to the frontend.
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONType, default=dict, nullable=False)

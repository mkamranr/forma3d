from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, CreatedAt, JSONType, UUIDPk

JOB_STATUSES = ("queued", "running", "succeeded", "failed", "cancelled")


class Job(UUIDPk, CreatedAt, Base):
    __tablename__ = "jobs"

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    operation_batch_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    job_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="queued", nullable=False, index=True)
    # Latest real stage event — never a fabricated percentage.
    progress: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    worker_id: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

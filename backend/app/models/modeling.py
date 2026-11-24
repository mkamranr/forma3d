from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, CreatedAt, JSONType, UUIDPk


class ModelingPlan(UUIDPk, CreatedAt, Base):
    __tablename__ = "modeling_plans"

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    message_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    plan_data: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False, index=True)


class OperationBatch(UUIDPk, CreatedAt, Base):
    """Immutable validated batch of DSL operations."""

    __tablename__ = "operation_batches"

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    modeling_plan_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    operations: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list, nullable=False)
    validation_result: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    execution_status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

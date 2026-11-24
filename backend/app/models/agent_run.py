from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, CreatedAt, JSONType, UUIDPk


class AgentRun(UUIDPk, CreatedAt, Base):
    __tablename__ = "agent_runs"

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    agent_name: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str | None] = mapped_column(String(128))
    input_metadata: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    output_metadata: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)

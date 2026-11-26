from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import JSON, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, CreatedAt, JSONType, UUIDPk

#: None is stored as SQL NULL (not JSON null), so purged prompts are really gone and `IS NULL` works.
NullableJSON = JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")


class AgentRun(UUIDPk, CreatedAt, Base):
    __tablename__ = "agent_runs"

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    #: node = a LangGraph step (ordered by `seq`), llm = one LLM call inside `node`, agent = a deterministic agent
    kind: Mapped[str] = mapped_column(String(16), default="agent", server_default="agent", nullable=False)
    node: Mapped[str | None] = mapped_column(String(64))
    seq: Mapped[int | None] = mapped_column(Integer)
    agent_name: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str | None] = mapped_column(String(128))
    input_metadata: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    output_metadata: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)
    #: Full LLM prompt and raw response (truncated); administrators only, purged after the trace retention.
    debug: Mapped[dict[str, Any] | None] = mapped_column(NullableJSON)

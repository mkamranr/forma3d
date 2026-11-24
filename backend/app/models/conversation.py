from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, CreatedAt, JSONType, Timestamps, UUIDPk


class Conversation(UUIDPk, Timestamps, Base):
    __tablename__ = "conversations"

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), default="Conversation", nullable=False)
    langgraph_thread_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)


class Message(UUIDPk, CreatedAt, Base):
    __tablename__ = "messages"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    intent: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="complete", nullable=False, index=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONType, default=dict, nullable=False)

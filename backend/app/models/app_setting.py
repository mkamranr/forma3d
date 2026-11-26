"""Runtime configuration set in the app (admin Configuration page); overrides the environment."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utcnow
from app.database.base import Base, JSONType, UTCDateTime


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONType, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow, nullable=False)
    #: Who changed it last (kept when the user is deleted).
    updated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

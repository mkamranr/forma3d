from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel


class SubmitResponse(BaseModel):
    job_id: uuid.UUID
    operation_batch_id: uuid.UUID
    status: str


class JobOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    job_type: str
    status: str
    progress: dict[str, Any]
    error_message: str | None
    error_code: str | None
    failed_operation_id: str | None
    operation_batch_id: uuid.UUID | None
    version_id: uuid.UUID | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


AssetKind = Literal["glb", "thumbnail", "blend", "inventory", "operations"]


class AssetOut(BaseModel):
    kind: str
    size: int
    sha256: str
    url: str


class VersionOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    parent_version_id: uuid.UUID | None
    version_number: int
    summary: str
    status: str
    operation_batch_id: uuid.UUID | None
    message_id: uuid.UUID | None
    is_current: bool
    object_count: int
    assets: list[AssetOut]
    created_at: datetime


class VersionDetail(VersionOut):
    inventory: dict[str, Any]

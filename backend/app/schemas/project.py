from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ProjectSort = Literal["updated_desc", "updated_asc", "name_asc", "name_desc", "created_desc"]
ProjectStatusFilter = Literal["active", "archived", "all"]


def _clean_name(v: str) -> str:
    v = v.strip()
    if not v:
        raise ValueError("name must not be blank")
    return v


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)

    _name = field_validator("name")(_clean_name)


class ProjectUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    status: Literal["active", "archived"] | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, v: str | None) -> str | None:
        return None if v is None else _clean_name(v)


class ProjectOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    status: str
    current_version_id: uuid.UUID | None
    version_count: int
    # Ownership-checked API URL; raw storage paths are never exposed.
    thumbnail_url: str | None
    created_at: datetime
    updated_at: datetime


class ProjectList(BaseModel):
    items: list[ProjectOut]
    total: int

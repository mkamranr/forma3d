"""Configurable complexity limits (spec §8.5). Defaults are conservative for CPU-only workers."""

from __future__ import annotations

import os

from pydantic import BaseModel, ConfigDict, Field


class ModelingLimits(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_operations_per_batch: int = Field(default=200, ge=1, le=2000)
    max_new_objects_per_batch: int = Field(default=100, ge=1, le=1000)
    max_scene_objects: int = Field(default=500, ge=1, le=5000)
    max_vertices_per_object: int = Field(default=200_000, ge=8)
    max_scene_vertices: int = Field(default=1_000_000, ge=8)
    max_scene_materials: int = Field(default=200, ge=1)
    max_bevel_segments: int = Field(default=10, ge=1, le=64)
    max_thumbnail_size: int = Field(default=1024, ge=64, le=4096)
    job_timeout_seconds: int = Field(default=300, ge=10, le=3600)
    max_output_file_bytes: int = Field(default=200 * 1024 * 1024, ge=1024)

    @classmethod
    def from_env(cls, prefix: str = "MODELING_") -> ModelingLimits:
        """Read overrides such as MODELING_MAX_SCENE_OBJECTS=800 from the environment."""
        values: dict[str, int] = {}
        for name in cls.model_fields:
            raw = os.environ.get(f"{prefix}{name.upper()}")
            if raw is not None:
                values[name] = int(raw)
        return cls(**values)

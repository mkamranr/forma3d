"""Constrained primitive types shared by every DSL schema."""

from __future__ import annotations

import math
import re
from typing import Annotated, Union

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

# Stable application ids: lowercase snake_case, never Blender-generated names.
ID_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
ObjectId = Annotated[str, StringConstraints(pattern=ID_PATTERN)]
MaterialId = Annotated[str, StringConstraints(pattern=ID_PATTERN)]
CollectionId = Annotated[str, StringConstraints(pattern=ID_PATTERN)]
OperationId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]{1,64}$")]
Token = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,47}$")]

HexColor = Annotated[str, StringConstraints(pattern=r"^#[0-9A-Fa-f]{6}$")]

_CODE_LIKE = re.compile(
    r"(`|__|\$\(|\bimport\s|\bexec\s*\(|\beval\s*\(|\bcompile\s*\(|\bbpy\.|\bos\.|\bsubprocess\b|"
    r"\bopen\s*\(|<script|\.\./|\.\.\\|^/|[A-Za-z]:\\|\x00)",
    re.IGNORECASE,
)


def reject_code_like(value: str) -> str:
    """Defense in depth: DSL strings are data, never code — reject anything that looks like it."""
    if _CODE_LIKE.search(value):
        raise ValueError("text must not contain code, shell syntax or file paths")
    return value


SafeText = Annotated[str, StringConstraints(max_length=500), AfterValidator(reject_code_like)]

# Display names: letters (any script), digits, spaces and a few punctuation marks only.
SafeName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=63, pattern=r"^[\w][\w .\-()·'&,#+]*$"),
    AfterValidator(reject_code_like),
]


def _finite(v: float) -> float:
    if not math.isfinite(v):
        raise ValueError("must be a finite number")
    return v


# Units: meters and degrees.
Coordinate = Annotated[float, Field(ge=-500.0, le=500.0), AfterValidator(_finite)]
Dimension = Annotated[float, Field(ge=0.0005, le=100.0), AfterValidator(_finite)]
Angle = Annotated[float, Field(ge=-3600.0, le=3600.0), AfterValidator(_finite)]
ScaleFactor = Annotated[float, Field(ge=0.001, le=1000.0), AfterValidator(_finite)]
UnitInterval = Annotated[float, Field(ge=0.0, le=1.0), AfterValidator(_finite)]

Vec3 = tuple[Coordinate, Coordinate, Coordinate]
Dim3 = tuple[Dimension, Dimension, Dimension]
Dim2 = tuple[Dimension, Dimension]
Angle3 = tuple[Angle, Angle, Angle]
Scale3 = tuple[ScaleFactor, ScaleFactor, ScaleFactor]

MetadataValue = Union[
    Annotated[str, StringConstraints(max_length=200), AfterValidator(reject_code_like)], int, float, bool
]


class StrictModel(BaseModel):
    """Unknown fields are rejected everywhere in the DSL (spec §8.5.9)."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

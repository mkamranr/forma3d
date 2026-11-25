"""Vision Agent (VLM): reads the reference photo(s) a user attached and turns them into a structured,
text-only description (parts, proportions, sizes, materials) for the Conversation and Planning agents.

Images never reach Blender or the planner: the planner only sees this description, so a photo is just
another way of writing a precise request, and everything downstream (DSL validation, QA) is unchanged.
"""

from __future__ import annotations

import base64
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..providers.base import ChatMessage, LLMProvider
from .llm import Result, structured_call

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"
SHAPES = ("box", "cylinder", "sphere", "cone", "torus", "curve", "plane")
FINISHES = ("matte", "satin", "glossy", "metallic", "glass", "emissive")
TEXTURES = ("none", "wood", "marble", "fabric", "brushed_metal", "concrete", "brick", "noise", "checker")


class Size(BaseModel):
    model_config = ConfigDict(extra="ignore")

    width: float = Field(default=0, ge=0, le=100)
    depth: float = Field(default=0, ge=0, le=100)
    height: float = Field(default=0, ge=0, le=100)

    def text(self) -> str:
        return f"{self.width:.3g} × {self.depth:.3g} × {self.height:.3g} m"


class VisualPart(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(max_length=80)
    shape: str = "box"
    count: int = Field(default=1, ge=1, le=100)
    size_m: Size = Field(default_factory=Size)
    position: str = Field(default="", max_length=300)
    orientation: str = Field(default="", max_length=200)
    material: str = Field(default="", max_length=80)


class VisualMaterial(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(max_length=80)
    color: str = Field(default="", max_length=16)
    finish: str = "matte"
    texture: str = "none"


class Description(BaseModel):
    model_config = ConfigDict(extra="ignore")

    is_object: bool = True
    object: str = Field(default="", max_length=120)
    summary: str = Field(default="", max_length=600)
    style: str = Field(default="", max_length=120)
    overall_size_m: Size = Field(default_factory=Size)
    parts: list[VisualPart] = Field(default_factory=list, max_length=40)
    materials: list[VisualMaterial] = Field(default_factory=list, max_length=20)
    notes: str = Field(default="", max_length=800)
    confidence: Literal["high", "medium", "low"] = "medium"

    def as_text(self) -> str:
        """Compact, planner-friendly rendering (also stored on the message and shown to the user)."""
        if not self.is_object:
            return f"No buildable object: {self.summary}".strip()
        size = self.overall_size_m
        lines = [
            f"{self.object or 'Object'}"
            + (f" ({self.style})" if self.style else "")
            + (f", about {size.text()} (W × D × H)" if size.height else "")
            + ".",
        ]
        if self.summary:
            lines.append(self.summary)
        if self.parts:
            lines.append("Parts:")
            for p in self.parts:
                bits = [f"{p.count} × " if p.count > 1 else "", f"{p.name}: {p.shape}, {p.size_m.text()}"]
                if p.position:
                    bits.append(f", {p.position}")
                if p.orientation and p.orientation.lower() not in ("upright", "none", ""):
                    bits.append(f", {p.orientation}")
                if p.material:
                    bits.append(f", {p.material}")
                lines.append("- " + "".join(bits))
        if self.materials:
            mats = []
            for m in self.materials:
                look = [m.color] if m.color else []
                if m.finish and m.finish != "matte":
                    look.append(m.finish)
                if m.texture and m.texture != "none":
                    look.append(f"{m.texture} texture")
                mats.append(f"{m.name} ({', '.join(look)})" if look else m.name)
            lines.append("Materials: " + "; ".join(mats) + ".")
        if self.notes:
            lines.append(f"Notes: {self.notes}")
        if self.confidence == "low":
            lines.append("(The photo was hard to read; sizes are rough guesses.)")
        return "\n".join(lines)


SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["is_object", "object", "summary", "overall_size_m", "parts", "materials"],
    "properties": {
        "is_object": {"type": "boolean"},
        "object": {"type": "string", "maxLength": 120},
        "summary": {"type": "string", "maxLength": 600},
        "style": {"type": "string", "maxLength": 120},
        "overall_size_m": {
            "type": "object",
            "required": ["width", "depth", "height"],
            "properties": {k: {"type": "number"} for k in ("width", "depth", "height")},
        },
        "parts": {
            "type": "array",
            "maxItems": 40,
            "items": {
                "type": "object",
                "required": ["name", "shape", "count", "size_m", "position", "material"],
                "properties": {
                    "name": {"type": "string", "maxLength": 80},
                    "shape": {"type": "string", "enum": list(SHAPES)},
                    "count": {"type": "integer", "minimum": 1, "maximum": 100},
                    "size_m": {
                        "type": "object",
                        "required": ["width", "depth", "height"],
                        "properties": {k: {"type": "number"} for k in ("width", "depth", "height")},
                    },
                    "position": {"type": "string", "maxLength": 300},
                    "orientation": {"type": "string", "maxLength": 200},
                    "material": {"type": "string", "maxLength": 80},
                },
            },
        },
        "materials": {
            "type": "array",
            "maxItems": 20,
            "items": {
                "type": "object",
                "required": ["name", "color", "finish", "texture"],
                "properties": {
                    "name": {"type": "string", "maxLength": 80},
                    "color": {"type": "string", "maxLength": 16},
                    "finish": {"type": "string", "enum": list(FINISHES)},
                    "texture": {"type": "string", "enum": list(TEXTURES)},
                },
            },
        },
        "notes": {"type": "string", "maxLength": 800},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
}


@lru_cache
def system_prompt() -> str:
    return (PROMPTS / "vision.md").read_text(encoding="utf-8")


def data_url(data: bytes, media_type: str) -> str:
    return f"data:{media_type};base64,{base64.b64encode(data).decode('ascii')}"


def check(d: Description) -> list[str]:
    if not d.is_object:
        return []
    problems = []
    if not d.parts:
        problems.append("list the object's visible parts in 'parts'")
    if d.overall_size_m.height <= 0 and d.overall_size_m.width <= 0:
        problems.append("estimate 'overall_size_m' in meters")
    bad = [p.name for p in d.parts if p.size_m.width <= 0 and p.size_m.depth <= 0 and p.size_m.height <= 0]
    if bad:
        problems.append(f"give a size in meters for every part (missing for {bad[:5]})")
    return problems


def describe(
    provider: LLMProvider,
    images: list[str],
    user_message: str,
    *,
    max_retries: int,
    max_tokens: int | None = None,
) -> Result[Description]:
    """`images` are data URLs. The user's words travel along: they may say which object to look at."""
    request = user_message.strip()
    text = (
        f"{len(images)} photo(s) attached."
        + (f' The user wrote: "{request[:800]}"' if request else "")
        + "\nDescribe the object to build. Answer with the JSON object only."
    )
    messages = [ChatMessage("system", system_prompt()), ChatMessage("user", text, images=images)]
    return structured_call(
        "vision",
        provider,
        messages,
        SCHEMA,
        Description,
        check=check,
        max_retries=max_retries,
        max_tokens=max_tokens or 3000,
    )

"""Bounded, conservative JSON extraction for LLM output. It never evaluates anything: it only
strips fences/comments/trailing commas and cuts the outermost object. Anything else is a retry."""

from __future__ import annotations

import json
import re
from typing import Any

MAX_INPUT = 200_000

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S | re.I)
_TRAILING_COMMA = re.compile(r",\s*([}\]])")
_LINE_COMMENT = re.compile(r"^\s*//.*$", re.M)


class JSONExtractError(ValueError):
    pass


def _outermost_object(text: str) -> str | None:
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def extract_json_object(text: str) -> dict[str, Any]:
    if not text or not text.strip():
        raise JSONExtractError("empty response")
    if len(text) > MAX_INPUT:
        raise JSONExtractError("response too large")
    candidates = [text.strip()]
    fence = _FENCE.search(text)
    if fence:
        candidates.insert(0, fence.group(1).strip())
    for cand in candidates:
        for variant in (cand, _outermost_object(cand)):
            if not variant:
                continue
            for attempt in (variant, _TRAILING_COMMA.sub(r"\1", _LINE_COMMENT.sub("", variant))):
                try:
                    value = json.loads(attempt)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    return value
    raise JSONExtractError("no valid JSON object found")

"""The Blender runtime registry must allow exactly the shared DSL allowlist — no more, no less.
(Parsed with ast because bl_runtime imports bpy, which only exists inside Blender.)"""

from __future__ import annotations

import ast
from pathlib import Path

from forma3d_shared.schemas.modeling_operation import ALLOWED_OPERATIONS

REGISTRY = Path(__file__).resolve().parents[1] / "app" / "bl_runtime" / "operation_registry.py"


def registry_keys() -> set[str]:
    tree = ast.parse(REGISTRY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None) == "HANDLERS":
            assert isinstance(node.value, ast.Dict)
            return {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
    raise AssertionError("HANDLERS not found")


def test_registry_matches_allowlist():
    assert registry_keys() == set(ALLOWED_OPERATIONS)


def test_runtime_never_evaluates_strings():
    runtime = REGISTRY.parent
    forbidden = ("eval(", "exec(", "compile(", "__import__", "importlib", "subprocess", "os.system")
    for path in runtime.glob("*.py"):
        src = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in src, f"{token} found in {path.name}"

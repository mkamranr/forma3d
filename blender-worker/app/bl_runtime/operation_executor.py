"""Executes a validated operation list in order. Stops at the first failure (the caller discards
the whole working copy — the committed version is never touched)."""

from __future__ import annotations

from collections.abc import Callable

from .context import OpError, RuntimeContext
from .operation_registry import HANDLERS


class ExecutionFailed(Exception):
    def __init__(self, operation_id: str, code: str, message: str) -> None:
        super().__init__(message)
        self.operation_id = operation_id
        self.code = code
        self.message = message


def execute(
    ctx: RuntimeContext, operations: list[dict], on_progress: Callable[[int, int, dict], None] | None = None
) -> None:
    total = len(operations)
    for index, op in enumerate(operations):
        op_id = str(op.get("operation_id", f"#{index}"))
        op_type = op.get("operation_type")
        handler = HANDLERS.get(op_type) if isinstance(op_type, str) else None
        if handler is None:
            raise ExecutionFailed(op_id, "unknown_operation", f"operation type '{op_type}' is not allowed")
        try:
            handler(ctx, op)
        except OpError as exc:
            raise ExecutionFailed(op_id, exc.code, exc.message) from exc
        except Exception as exc:  # unexpected Blender error: report, never partially commit
            raise ExecutionFailed(op_id, "execution_error", f"{type(exc).__name__}: {exc}"[:500]) from exc
        if on_progress:
            on_progress(index + 1, total, op)

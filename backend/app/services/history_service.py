"""Undo / redo / compare over immutable versions (spec §6.5).

Versions form a tree through `parent_version_id`; the project head is a pointer into it.
- undo  -> the head's parent
- redo  -> the head's most recent child (after an undo that is the version you came from; after
           undo + a new edit it is the new branch)
- a new edit after an undo creates a branch: its parent is the restored version.
Nothing is ever copied or deleted.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models import Project, SceneVersion
from forma3d_shared.inventory_diff import diff_inventories
from forma3d_shared.schemas.scene_object import SceneInventory


async def head_of(session: AsyncSession, project: Project) -> SceneVersion | None:
    return await session.get(SceneVersion, project.current_version_id) if project.current_version_id else None


async def undo_target(session: AsyncSession, project: Project) -> SceneVersion | None:
    head = await head_of(session, project)
    if head is None or head.parent_version_id is None:
        return None
    parent = await session.get(SceneVersion, head.parent_version_id)
    return parent if parent is not None and parent.status == "committed" else None


async def redo_target(session: AsyncSession, project: Project) -> SceneVersion | None:
    if project.current_version_id is None:
        return None
    return (
        await session.execute(
            select(SceneVersion)
            .where(
                SceneVersion.project_id == project.id,
                SceneVersion.parent_version_id == project.current_version_id,
                SceneVersion.status == "committed",
            )
            .order_by(SceneVersion.created_at.desc(), SceneVersion.version_number.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def step_target(session: AsyncSession, project: Project, direction: str) -> SceneVersion:
    if direction == "undo":
        target = await undo_target(session, project)
        if target is None:
            raise ConflictError("There is no earlier version to go back to", code="nothing_to_undo")
    else:
        target = await redo_target(session, project)
        if target is None:
            raise ConflictError("There is nothing to redo", code="nothing_to_redo")
    return target


async def version_by_id(session: AsyncSession, project: Project, version_id: uuid.UUID) -> SceneVersion:
    v = await session.get(SceneVersion, version_id)
    if v is None or v.project_id != project.id or v.status != "committed":
        raise NotFoundError("Version not found")
    return v


async def compare(session: AsyncSession, project: Project, base_id: uuid.UUID, target_id: uuid.UUID) -> dict[str, Any]:
    base = await version_by_id(session, project, base_id)
    target = await version_by_id(session, project, target_id)
    diff = diff_inventories(
        SceneInventory.model_validate(base.object_inventory or {}),
        SceneInventory.model_validate(target.object_inventory or {}),
    )
    return {
        "base": {"id": base.id, "version_number": base.version_number, "summary": base.summary or ""},
        "target": {"id": target.id, "version_number": target.version_number, "summary": target.summary or ""},
        **diff,
    }

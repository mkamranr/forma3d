"""Direct UI edits (spec §15.7). Each edit is compiled into a small DSL batch and submitted through
`ModelingService.submit_batch`, so it gets exactly the same validation, locking, Blender execution
and versioning as LLM-generated operations. No LLM is involved."""

from __future__ import annotations

import re
import uuid
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError, ConflictError, NotFoundError
from app.core.time import utcnow
from app.models import Job, OperationBatch, Project, SceneObject, SceneVersion
from app.repositories.modeling import ModelingRepository
from app.repositories.projects import ProjectRepository
from app.schemas.scene import MaterialPatch, NewObject, ObjectMaterial, ObjectPatch
from app.services.locks import ProjectLock
from app.services.modeling_service import ModelingService
from app.services.storage import ProjectStorage
from forma3d_shared import DSL_SCHEMA_VERSION
from forma3d_shared.examples import desk_batch
from forma3d_shared.schemas.scene_object import SceneInventory

_ID_CLEAN = re.compile(r"[^a-z0-9_]+")

PRIMITIVE_DEFAULTS: dict[str, tuple[str, str, dict[str, Any]]] = {
    "cube": ("create_cube", "Cube", {"dimensions": [0.5, 0.5, 0.5]}),
    "sphere": ("create_sphere", "Sphere", {"radius": 0.25}),
    "cylinder": ("create_cylinder", "Cylinder", {"radius": 0.2, "depth": 0.5}),
    "cone": ("create_cone", "Cone", {"radius_bottom": 0.25, "depth": 0.5}),
    "plane": ("create_plane", "Plane", {"size": 1.0}),
    "torus": ("create_torus", "Torus", {"major_radius": 0.3, "minor_radius": 0.08}),
}


def unique_id(base: str, taken: set[str]) -> str:
    base = _ID_CLEAN.sub("_", base.lower()).strip("_") or "obj"
    if not base[0].isalpha():
        base = f"o_{base}"
    base = base[:56]
    if base not in taken:
        return base
    n = 2
    while f"{base}_{n}" in taken:
        n += 1
    return f"{base}_{n}"


def _batch(summary: str, ops: list[dict[str, Any]]) -> dict[str, Any]:
    for i, op in enumerate(ops):
        op.setdefault("operation_id", f"op_{i + 1}")
    return {"schema_version": DSL_SCHEMA_VERSION, "summary": summary[:500], "operations": ops}


def _op(op_type: str, target: str | None = None, **params: Any) -> dict[str, Any]:
    op: dict[str, Any] = {"operation_type": op_type, "parameters": params}
    if target is not None:
        op["target_object_id"] = target
    return op


class SceneService:
    def __init__(self, session: AsyncSession, redis: Redis, settings: Settings, storage: ProjectStorage) -> None:
        self.session = session
        self.redis = redis
        self.settings = settings
        self.storage = storage
        self.modeling = ModelingService(session, redis, settings, storage)
        self.repo = ModelingRepository(session)

    async def _project(self, user_id: uuid.UUID, project_id: uuid.UUID) -> Project:
        project = await ProjectRepository(self.session).get_owned(user_id, project_id)
        if project is None:
            raise NotFoundError("Project not found")
        return project

    async def _inventory(self, project: Project) -> SceneInventory:
        return await self.modeling.head_inventory(project)

    async def _submit(self, user_id: uuid.UUID, project: Project, batch: dict[str, Any]) -> tuple[Job, OperationBatch]:
        return await self.modeling.submit_batch(user_id, project.id, batch)

    # ------------------------------------------------------------------------------ reads
    async def scene(self, user_id: uuid.UUID, project_id: uuid.UUID) -> tuple[Project, SceneVersion | None, Job | None]:
        project = await self._project(user_id, project_id)
        head = await self.session.get(SceneVersion, project.current_version_id) if project.current_version_id else None
        stmt = (
            select(Job)
            .where(Job.project_id == project.id, Job.status.in_(("queued", "running")))
            # A chat turn owns its modeling job: resume the agent job, it carries the reply.
            .order_by((Job.job_type == "agent").desc(), Job.created_at.desc())
            .limit(1)
        )
        active = (await self.session.execute(stmt)).scalar_one_or_none()
        return project, head, active

    # ------------------------------------------------------------------------------ edits
    async def add_object(self, user_id: uuid.UUID, project_id: uuid.UUID, body: NewObject):
        project = await self._project(user_id, project_id)
        inv = await self._inventory(project)
        op_type, default_name, params = PRIMITIVE_DEFAULTS[body.kind]
        oid = unique_id(body.kind, {o.object_id for o in inv.objects})
        name = body.name or default_name
        params = {**params, "name": name, "location": list(body.location)}
        return await self._submit(user_id, project, _batch(f"Add {name}", [_op(op_type, oid, **params)]))

    async def edit_object(self, user_id: uuid.UUID, project_id: uuid.UUID, object_id: str, patch: ObjectPatch):
        project = await self._project(user_id, project_id)
        obj = (await self._inventory(project)).object_map().get(object_id)
        if obj is None:
            raise NotFoundError("Object not found in the current version")
        ops: list[dict[str, Any]] = []
        changes: list[str] = []
        if patch.name is not None and patch.name != obj.name:
            ops.append(_op("rename_object", object_id, name=patch.name))
            changes.append(f"renamed to {patch.name}")
        if patch.dimensions is not None:
            ops.append(_op("set_dimensions", object_id, dimensions=list(patch.dimensions)))
            changes.append("resized")
        if patch.rotation is not None:
            ops.append(_op("rotate_object", object_id, rotation=list(patch.rotation)))
            changes.append("rotated")
        if patch.location is not None:
            ops.append(_op("move_object", object_id, location=list(patch.location)))
            changes.append("moved")
        if patch.visible is not None and patch.visible != obj.visible:
            ops.append(_op("set_visibility", object_id, visible=patch.visible))
            changes.append("shown" if patch.visible else "hidden")
        if patch.material_id is not None:
            ops.append(_op("assign_material", object_id, material_id=patch.material_id))
            changes.append(f"material set to {patch.material_id}")
        if not ops:
            raise AppError("Nothing to change", code="no_changes", status_code=422)
        summary = f"{obj.name}: {', '.join(changes)}"
        return await self._submit(user_id, project, _batch(summary[:1].upper() + summary[1:], ops))

    async def delete_object(self, user_id: uuid.UUID, project_id: uuid.UUID, object_id: str, recursive: bool):
        project = await self._project(user_id, project_id)
        obj = (await self._inventory(project)).object_map().get(object_id)
        if obj is None:
            raise NotFoundError("Object not found in the current version")
        op = _op("delete_object", object_id, recursive=recursive)
        return await self._submit(user_id, project, _batch(f"Delete {obj.name}", [op]))

    async def edit_material(self, user_id: uuid.UUID, project_id: uuid.UUID, material_id: str, patch: MaterialPatch):
        project = await self._project(user_id, project_id)
        mat = next((m for m in (await self._inventory(project)).materials if m.material_id == material_id), None)
        if mat is None:
            raise NotFoundError("Material not found in the current version")
        params = patch.model_dump(exclude_none=True)
        if not params:
            raise AppError("Nothing to change", code="no_changes", status_code=422)
        return await self._submit(
            user_id,
            project,
            _batch(f"Update material {mat.name}", [_op("update_material", material_id=material_id, **params)]),
        )

    async def give_object_material(
        self, user_id: uuid.UUID, project_id: uuid.UUID, object_id: str, body: ObjectMaterial
    ):
        """Create a material dedicated to one object (e.g. 'make only this leg red') and assign it."""
        project = await self._project(user_id, project_id)
        inv = await self._inventory(project)
        obj = inv.object_map().get(object_id)
        if obj is None:
            raise NotFoundError("Object not found in the current version")
        mid = unique_id(f"{object_id}_mat", {m.material_id for m in inv.materials})
        params = body.model_dump(exclude_none=True)
        params.setdefault("name", f"{obj.name} material")
        ops = [
            _op("create_material", material_id=mid, **params),
            {**_op("assign_material", object_id, material_id=mid), "dependencies": ["op_1"]},
        ]
        return await self._submit(user_id, project, _batch(f"New material for {obj.name}", ops))

    async def starter_scene(self, user_id: uuid.UUID, project_id: uuid.UUID):
        project = await self._project(user_id, project_id)
        if project.current_version_id is not None:
            raise ConflictError("The project already has a scene", code="scene_exists")
        return await self._submit(user_id, project, desk_batch())

    # ------------------------------------------------------------------------------ restore
    async def restore_version(self, user_id: uuid.UUID, project_id: uuid.UUID, version_id: uuid.UUID) -> Project:
        """Point the head at an earlier immutable version. Nothing is copied or deleted; the next
        edit branches from the restored version (its parent_version_id)."""
        project = await self._project(user_id, project_id)
        version = await self.repo.get_version(project.id, version_id)
        if version is None or version.status != "committed":
            raise NotFoundError("Version not found")
        if project.current_version_id == version.id:
            return project
        lock = ProjectLock(self.redis)
        token = uuid.uuid4()
        if not await lock.acquire(project.id, token, 30):
            raise ConflictError("Another change to this project is still being built", code="project_busy")
        try:
            inv = SceneInventory.model_validate(version.object_inventory)
            await self.repo.replace_scene_objects(
                project.id,
                [
                    SceneObject(
                        project_id=project.id,
                        blender_object_id=o.object_id,
                        parent_object_id=o.parent_id,
                        object_name=o.name,
                        object_type=o.type,
                        component_type=o.component_type,
                        properties=o.model_dump(
                            mode="json", exclude={"object_id", "name", "type", "component_type", "parent_id"}
                        ),
                    )
                    for o in inv.objects
                ],
            )
            project.current_version_id = version.id
            project.thumbnail_path = version.thumbnail_path or project.thumbnail_path
            project.updated_at = utcnow()
            await self.session.commit()
        finally:
            await lock.release(project.id, token)
        await self.session.refresh(project)
        return project

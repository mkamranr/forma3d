"""Repositories for jobs, batches, versions and assets. Every user-facing lookup joins through
`projects.user_id` so ownership is enforced at the query level."""

from __future__ import annotations

import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Asset, Job, OperationBatch, Project, SceneObject, SceneVersion

VERSION_ASSET_KINDS = ("blend", "glb", "thumbnail", "inventory", "operations")


class ModelingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # jobs -------------------------------------------------------------------------------
    async def get_job(self, job_id: uuid.UUID, *, for_update: bool = False) -> Job | None:
        stmt = select(Job).where(Job.id == job_id)
        if for_update:
            stmt = stmt.with_for_update()
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_owned_job(self, user_id: uuid.UUID, job_id: uuid.UUID) -> Job | None:
        stmt = (
            select(Job)
            .join(Project, Project.id == Job.project_id)
            .where(Job.id == job_id, Project.user_id == user_id, Project.status != "deleted")
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_batch(self, batch_id: uuid.UUID) -> OperationBatch | None:
        return await self.session.get(OperationBatch, batch_id)

    # versions ---------------------------------------------------------------------------
    async def get_version(self, project_id: uuid.UUID, version_id: uuid.UUID) -> SceneVersion | None:
        stmt = select(SceneVersion).where(SceneVersion.id == version_id, SceneVersion.project_id == project_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_versions(self, project_id: uuid.UUID) -> list[SceneVersion]:
        stmt = (
            select(SceneVersion)
            .where(SceneVersion.project_id == project_id, SceneVersion.status == "committed")
            .order_by(SceneVersion.version_number.desc())
        )
        return list((await self.session.execute(stmt)).scalars())

    async def max_version_number(self, project_id: uuid.UUID) -> int:
        stmt = select(func.coalesce(func.max(SceneVersion.version_number), 0)).where(
            SceneVersion.project_id == project_id
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def version_exists(self, project_id: uuid.UUID, version_number: int) -> bool:
        stmt = select(SceneVersion.id).where(
            SceneVersion.project_id == project_id, SceneVersion.version_number == version_number
        )
        return (await self.session.execute(stmt)).first() is not None

    # assets -----------------------------------------------------------------------------
    async def assets_for_versions(self, version_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[Asset]]:
        if not version_ids:
            return {}
        # Only a version's own artifacts; renders and exports made from it are listed separately.
        stmt = select(Asset).where(Asset.version_id.in_(version_ids), Asset.asset_type.in_(VERSION_ASSET_KINDS))
        rows = (await self.session.execute(stmt)).scalars()
        out: dict[uuid.UUID, list[Asset]] = {}
        for a in rows:
            out.setdefault(a.version_id, []).append(a)  # type: ignore[arg-type]
        return out

    async def get_asset(self, version_id: uuid.UUID, kind: str) -> Asset | None:
        stmt = select(Asset).where(Asset.version_id == version_id, Asset.asset_type == kind)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    # scene objects ----------------------------------------------------------------------
    async def replace_scene_objects(self, project_id: uuid.UUID, rows: list[SceneObject]) -> None:
        await self.session.execute(delete(SceneObject).where(SceneObject.project_id == project_id))
        self.session.add_all(rows)

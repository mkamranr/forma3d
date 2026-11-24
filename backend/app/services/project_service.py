"""Project use-cases. Ownership is enforced through the repository on every call."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.core.time import utcnow
from app.models import Project
from app.repositories.projects import ProjectRepository
from app.schemas.project import (
    ProjectCreate,
    ProjectList,
    ProjectOut,
    ProjectSort,
    ProjectStatusFilter,
    ProjectUpdate,
)

log = get_logger(__name__)


def to_out(project: Project, version_count: int) -> ProjectOut:
    return ProjectOut(
        id=project.id,
        name=project.name,
        description=project.description,
        status=project.status,
        current_version_id=project.current_version_id,
        version_count=version_count,
        thumbnail_url=(f"/api/v1/projects/{project.id}/thumbnail" if project.thumbnail_path else None),
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


class ProjectService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = ProjectRepository(session)

    async def _owned(self, user_id: uuid.UUID, project_id: uuid.UUID) -> Project:
        project = await self.repo.get_owned(user_id, project_id)
        if project is None:
            # 404 (not 403) so other users' project ids are not disclosed.
            raise NotFoundError("Project not found")
        return project

    async def list(
        self,
        user_id: uuid.UUID,
        *,
        q: str | None = None,
        status: ProjectStatusFilter = "active",
        sort: ProjectSort = "updated_desc",
        limit: int = 50,
        offset: int = 0,
    ) -> ProjectList:
        rows, total = await self.repo.list_owned(
            user_id, q=q, status=status, sort=sort, limit=limit, offset=offset
        )
        return ProjectList(items=[to_out(p, c) for p, c in rows], total=total)

    async def get(self, user_id: uuid.UUID, project_id: uuid.UUID) -> ProjectOut:
        project = await self._owned(user_id, project_id)
        return to_out(project, await self.repo.version_count(project.id))

    async def create(self, user_id: uuid.UUID, data: ProjectCreate) -> ProjectOut:
        project = await self.repo.create(user_id=user_id, name=data.name, description=data.description)
        await self.session.commit()
        await self.session.refresh(project)
        log.info("project.created", user_id=str(user_id), project_id=str(project.id))
        return to_out(project, 0)

    async def update(self, user_id: uuid.UUID, project_id: uuid.UUID, data: ProjectUpdate) -> ProjectOut:
        project = await self._owned(user_id, project_id)
        changes = data.model_dump(exclude_unset=True, exclude_none=True)
        for field, value in changes.items():
            setattr(project, field, value)
        if changes:
            project.updated_at = utcnow()
        await self.session.commit()
        await self.session.refresh(project)
        return to_out(project, await self.repo.version_count(project.id))

    async def delete(self, user_id: uuid.UUID, project_id: uuid.UUID) -> None:
        """Soft delete: hidden immediately; files are purged by the retention job (Phase 8)."""
        project = await self._owned(user_id, project_id)
        project.status = "deleted"
        project.deleted_at = utcnow()
        await self.session.commit()
        log.info("project.deleted", user_id=str(user_id), project_id=str(project_id))

    async def duplicate(self, user_id: uuid.UUID, project_id: uuid.UUID) -> ProjectOut:
        """Phase 1 copies metadata only. Copying the head version's files is added with
        versioned storage in Phase 2 (documented limitation)."""
        source = await self._owned(user_id, project_id)
        name = f"{source.name} (copy)"[:120]
        project = await self.repo.create(user_id=user_id, name=name, description=source.description)
        await self.session.commit()
        await self.session.refresh(project)
        return to_out(project, 0)

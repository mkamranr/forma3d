"""Project repository. Every query is scoped by the owning user_id (spec §11.12)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project, SceneVersion
from app.schemas.project import ProjectSort, ProjectStatusFilter

_SORTS: dict[str, Any] = {
    "updated_desc": Project.updated_at.desc(),
    "updated_asc": Project.updated_at.asc(),
    "name_asc": func.lower(Project.name).asc(),
    "name_desc": func.lower(Project.name).desc(),
    "created_desc": Project.created_at.desc(),
}


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class ProjectRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _version_count():
        return (
            select(func.count(SceneVersion.id))
            .where(SceneVersion.project_id == Project.id, SceneVersion.status == "committed")
            .correlate(Project)
            .scalar_subquery()
        )

    async def get_owned(self, user_id: uuid.UUID, project_id: uuid.UUID) -> Project | None:
        result = await self.session.execute(
            select(Project).where(
                Project.id == project_id, Project.user_id == user_id, Project.status != "deleted"
            )
        )
        return result.scalar_one_or_none()

    async def version_count(self, project_id: uuid.UUID) -> int:
        result = await self.session.execute(
            select(func.count(SceneVersion.id)).where(
                SceneVersion.project_id == project_id, SceneVersion.status == "committed"
            )
        )
        return int(result.scalar_one())

    async def list_owned(
        self,
        user_id: uuid.UUID,
        *,
        q: str | None,
        status: ProjectStatusFilter,
        sort: ProjectSort,
        limit: int,
        offset: int,
    ) -> tuple[list[tuple[Project, int]], int]:
        conditions = [Project.user_id == user_id]
        if status == "all":
            conditions.append(Project.status != "deleted")
        else:
            conditions.append(Project.status == status)
        if q:
            pattern = f"%{_escape_like(q.strip().lower())}%"
            conditions.append(
                or_(
                    func.lower(Project.name).like(pattern, escape="\\"),
                    func.lower(Project.description).like(pattern, escape="\\"),
                )
            )

        total = (await self.session.execute(select(func.count(Project.id)).where(*conditions))).scalar_one()
        rows = await self.session.execute(
            select(Project, self._version_count().label("version_count"))
            .where(*conditions)
            .order_by(_SORTS[sort], Project.id)
            .limit(limit)
            .offset(offset)
        )
        return [(p, int(c)) for p, c in rows.all()], int(total)

    async def create(self, *, user_id: uuid.UUID, name: str, description: str) -> Project:
        project = Project(user_id=user_id, name=name, description=description, status="active")
        self.session.add(project)
        await self.session.flush()
        return project

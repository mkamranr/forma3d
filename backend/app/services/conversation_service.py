"""Conversations and agent jobs (Phase 4: linear planner pipeline; LangGraph arrives in Phase 5).

Flow: user message -> agent job on `jobs:agent` -> agent worker loads context (internal API) ->
LLM plan -> DSL validation -> `submit_plan` (same `submit_batch` path as every edit) -> Blender ->
version -> agent `complete` stores the assistant reply linked to the version."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError, ConflictError, NotFoundError, ServiceUnavailableError
from app.core.logging import get_logger
from app.core.time import utcnow
from app.models import AgentRun, Conversation, Job, Message, ModelingPlan, OperationBatch, Project, SceneVersion
from app.repositories.modeling import ModelingRepository
from app.repositories.projects import ProjectRepository
from app.services import history_service
from app.services.events import EventPublisher
from app.services.locks import AdmissionGate
from app.services.modeling_service import ModelingService
from app.services.output_service import OutputService
from app.services.storage import ProjectStorage
from forma3d_shared import DSL_SCHEMA_VERSION
from forma3d_shared.schemas.agent import (
    AGENT_JOB_STREAM,
    AgentCompleteRequest,
    AgentContext,
    AgentJobSpec,
    AgentRunRecord,
    ChatTurn,
    RestoreRequest,
    SubmitPlanRequest,
)
from forma3d_shared.schemas.events import cancel_flag_key
from forma3d_shared.schemas.job import DOWNLOAD_FORMATS, RenderSettings
from forma3d_shared.schemas.scene_object import SceneInventory

log = get_logger(__name__)

HISTORY_TURNS = 12
ACTIVE = ("queued", "running")
#: Jobs that change the scene. Renders and exports only read a version and never block editing.
SCENE_JOB_TYPES = ("agent", "modeling")


class ConversationService:
    def __init__(self, session: AsyncSession, redis: Redis, settings: Settings) -> None:
        self.session = session
        self.redis = redis
        self.settings = settings
        self.events = EventPublisher(redis)

    # ------------------------------------------------------------------ user-facing
    async def _project(self, user_id: uuid.UUID, project_id: uuid.UUID) -> Project:
        project = await ProjectRepository(self.session).get_owned(user_id, project_id)
        if project is None:
            raise NotFoundError("Project not found")
        return project

    async def get_or_create(self, user_id: uuid.UUID, project_id: uuid.UUID) -> Conversation:
        project = await self._project(user_id, project_id)
        stmt = (
            select(Conversation)
            .where(Conversation.project_id == project.id, Conversation.user_id == user_id)
            .order_by(Conversation.created_at)
            .limit(1)
        )
        conv = (await self.session.execute(stmt)).scalar_one_or_none()
        if conv is None:
            conv = Conversation(
                project_id=project.id, user_id=user_id, title=project.name[:200], langgraph_thread_id=uuid.uuid4().hex
            )
            self.session.add(conv)
            await self.session.commit()
        return conv

    async def messages(self, conv: Conversation, limit: int = 200) -> list[Message]:
        stmt = (
            select(Message)
            .where(Message.conversation_id == conv.id)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(limit)
        )
        return list(reversed(list((await self.session.execute(stmt)).scalars())))

    async def _next_timestamp(self, conversation_id: uuid.UUID) -> datetime:
        """Strictly increasing per conversation, so ordering never depends on clock resolution
        (Windows clocks tick every ~15 ms; a reply and the next prompt can otherwise tie)."""
        last = (
            await self.session.execute(
                select(func.max(Message.created_at)).where(Message.conversation_id == conversation_id)
            )
        ).scalar_one_or_none()
        now = utcnow()
        if last is not None:
            if last.tzinfo is None:
                last = last.replace(tzinfo=now.tzinfo)
            if last >= now:
                return last + timedelta(milliseconds=1)
        return now

    async def post_message(
        self,
        user_id: uuid.UUID,
        project_id: uuid.UUID,
        content: str,
        selected_object_id: str | None,
        selected_object_ids: list[str] | None = None,
    ) -> tuple[Message, Job]:
        # Full selection, primary (most recently selected) object last.
        selection = [s for s in dict.fromkeys(selected_object_ids or []) if s != selected_object_id]
        if selected_object_id:
            selection.append(selected_object_id)
        selected_object_id = selection[-1] if selection else None
        async with AdmissionGate(self.redis, project_id):  # atomic "nothing running" -> create the turn
            message, job, spec = await self._admit(user_id, project_id, content, selected_object_id, selection)
        try:
            await self.redis.xadd(AGENT_JOB_STREAM, {"job": spec.model_dump_json()})
        except Exception as exc:
            job.status, job.error_message = "failed", "Assistant queue unavailable"
            await self.session.commit()
            raise ServiceUnavailableError("The assistant is unavailable, please try again") from exc
        await self.events.publish(job.id, "job.queued", stage="queued", message="Waiting for the assistant")
        return message, job

    async def _admit(
        self,
        user_id: uuid.UUID,
        project_id: uuid.UUID,
        content: str,
        selected_object_id: str | None,
        selection: list[str],
    ) -> tuple[Message, Job, AgentJobSpec]:
        project = await self._project(user_id, project_id)
        if project.status != "active":
            raise ConflictError("Archived projects are read-only; unarchive it first", code="project_archived")
        active = await self.session.execute(
            select(Job.id)
            .where(Job.project_id == project.id, Job.status.in_(ACTIVE), Job.job_type.in_(SCENE_JOB_TYPES))
            .limit(1)
        )
        if active.first() is not None:
            raise ConflictError("Another change to this project is still being built", code="project_busy")

        conv = await self.get_or_create(user_id, project_id)
        message = Message(
            conversation_id=conv.id,
            role="user",
            content=content,
            status="complete",
            created_at=await self._next_timestamp(conv.id),
            metadata_={"selected_object_id": selected_object_id, "selected_object_ids": selection} if selection else {},
        )
        self.session.add(message)
        await self.session.flush()
        job = Job(
            project_id=project.id,
            job_type="agent",
            status="queued",
            progress={"stage": "queued", "message": "Waiting for the assistant"},
            payload={
                "conversation_id": str(conv.id),
                "message_id": str(message.id),
                "user_id": str(user_id),
                "selected_object_id": selected_object_id,
                "selected_object_ids": selection,
            },
        )
        self.session.add(job)
        await self.session.flush()
        message.metadata_ = {**message.metadata_, "agent_job_id": str(job.id)}
        conv.updated_at = utcnow()
        await self.session.commit()

        spec = AgentJobSpec(job_id=job.id, project_id=project.id, conversation_id=conv.id, message_id=message.id)
        return message, job, spec

    # ------------------------------------------------------------------ worker-facing
    async def _agent_job(self, job_id: uuid.UUID, *, for_update: bool = False) -> Job:
        job = await ModelingRepository(self.session).get_job(job_id, for_update=for_update)
        if job is None or job.job_type != "agent":
            raise NotFoundError("Agent job not found")
        return job

    async def context(self, job_id: uuid.UUID) -> AgentContext:
        job = await self._agent_job(job_id)
        project = await self.session.get(Project, job.project_id)
        user_message = await self.session.get(Message, uuid.UUID(job.payload["message_id"]))
        assert project is not None and user_message is not None
        head = await self.session.get(SceneVersion, project.current_version_id) if project.current_version_id else None
        prior = (
            await self.session.execute(
                select(Message)
                .where(
                    Message.conversation_id == user_message.conversation_id,
                    Message.created_at <= user_message.created_at,
                    Message.id != user_message.id,
                    Message.role.in_(("user", "assistant")),
                )
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(HISTORY_TURNS)
            )
        ).scalars()
        history = [ChatTurn(role=m.role, content=m.content[:2000]) for m in reversed(list(prior))]  # type: ignore[arg-type]
        conversation = await self.session.get(Conversation, user_message.conversation_id)
        numbers = (
            await self.session.execute(
                select(SceneVersion.version_number)
                .where(SceneVersion.project_id == project.id, SceneVersion.status == "committed")
                .order_by(SceneVersion.version_number.desc())
                .limit(50)
            )
        ).scalars()
        return AgentContext(
            job_id=job.id,
            project_id=project.id,
            project_name=project.name,
            project_description=project.description or "",
            version_number=head.version_number if head else None,
            inventory=SceneInventory.model_validate(head.object_inventory) if head else SceneInventory(),
            history=history,
            user_message=user_message.content,
            selected_object_id=job.payload.get("selected_object_id"),
            selected_object_ids=job.payload.get("selected_object_ids")
            or ([job.payload["selected_object_id"]] if job.payload.get("selected_object_id") else []),
            thread_id=conversation.langgraph_thread_id if conversation else "",
            head_version_id=head.id if head else None,
            version_numbers=list(numbers),
        )

    async def submit_plan(self, job_id: uuid.UUID, body: SubmitPlanRequest, storage: ProjectStorage):
        job = await self._agent_job(job_id)
        if job.status != "running":
            raise ConflictError(f"Agent job is {job.status}", code="job_not_running")
        if await self.redis.exists(cancel_flag_key(job.id)):
            raise ConflictError("The request was cancelled", code="cancelled")
        attempts: dict[str, str] = dict(job.payload.get("attempts") or {})
        if str(body.attempt) in attempts:  # retried submission (worker resumed after a crash)
            repo = ModelingRepository(self.session)
            existing = await repo.get_job(uuid.UUID(attempts[str(body.attempt)]))
            if existing is not None and existing.operation_batch_id:
                existing_batch = await repo.get_batch(existing.operation_batch_id)
                if existing_batch is not None:
                    return existing, existing_batch
        project = await self.session.get(Project, job.project_id)
        assert project is not None
        message_id = uuid.UUID(job.payload["message_id"])
        plan = ModelingPlan(
            project_id=project.id,
            conversation_id=uuid.UUID(job.payload["conversation_id"]),
            message_id=message_id,
            plan_data={"summary": body.summary, "operations": body.operations, **body.plan},
            status="submitted",
        )
        self.session.add(plan)
        await self.session.commit()
        modeling = ModelingService(self.session, self.redis, self.settings, storage)
        batch_payload: dict[str, Any] = {
            "schema_version": DSL_SCHEMA_VERSION,
            "summary": body.summary,
            "operations": body.operations,
        }
        try:
            mjob, batch = await modeling.submit_batch(
                project.user_id,
                project.id,
                batch_payload,
                message_id=message_id,
                agent_job_id=job.id,
                defer_commit=body.defer_commit,
            )
        except Exception:
            plan.status = "rejected"
            await self.session.commit()
            raise
        plan.status = "executing"
        batch.modeling_plan_id = plan.id
        attempts[str(body.attempt)] = str(mjob.id)
        job.payload = {
            **job.payload,
            "modeling_job_id": str(mjob.id),
            "modeling_plan_id": str(plan.id),
            "attempts": attempts,
        }
        await self.session.commit()
        return mjob, batch

    async def restore(self, job_id: uuid.UUID, body: RestoreRequest) -> SceneVersion:
        """Version manager for chat turns ("undo", "go back to version 2"): moves the head pointer."""
        job = await self._agent_job(job_id)
        if job.status != "running":
            raise ConflictError(f"Agent job is {job.status}", code="job_not_running")
        project = await self.session.get(Project, job.project_id)
        assert project is not None
        if body.undo or body.redo:
            target = await history_service.step_target(self.session, project, "undo" if body.undo else "redo")
        else:
            target = (
                await self.session.execute(
                    select(SceneVersion).where(
                        SceneVersion.project_id == project.id,
                        SceneVersion.version_number == body.version_number,
                        SceneVersion.status == "committed",
                    )
                )
            ).scalar_one_or_none()
        if target is None:
            raise NotFoundError("Version not found")
        from app.services.scene_service import SceneService  # local: scene_service imports this module's deps

        scenes = SceneService(self.session, self.redis, self.settings, ProjectStorage(self.settings.data_root))
        await scenes.restore_version(project.user_id, project.id, target.id, by_agent=True)
        return target

    async def record_run(self, job_id: uuid.UUID, body: AgentRunRecord) -> None:
        job = await self._agent_job(job_id)
        self.session.add(
            AgentRun(
                project_id=job.project_id,
                conversation_id=uuid.UUID(job.payload["conversation_id"]),
                job_id=job.id,
                agent_name=body.agent_name,
                model_name=body.model_name,
                input_metadata=body.input_metadata,
                output_metadata=body.output_metadata,
                status=body.status,
                duration_ms=body.duration_ms,
                error_message=body.error_message,
            )
        )
        await self.session.commit()

    async def complete(self, job_id: uuid.UUID, body: AgentCompleteRequest) -> Message | None:
        job = await self._agent_job(job_id, for_update=True)
        if job.status not in ACTIVE:
            log.info("agent.complete_ignored", job_id=str(job.id), status=job.status)
            return None
        metadata: dict[str, Any] = {**body.metadata, "agent_job_id": str(job.id)}
        version_number = None
        if body.modeling_job_id:
            mjob = await ModelingRepository(self.session).get_job(body.modeling_job_id)
            if mjob is not None and mjob.project_id == job.project_id:
                metadata["modeling_job_id"] = str(mjob.id)
                vid = mjob.payload.get("result_version_id")
                if vid:
                    version = await self.session.get(SceneVersion, uuid.UUID(vid))
                    if version is not None:
                        version_number = version.version_number
                        metadata.update(version_id=vid, version_number=version_number)
                        plan_id = job.payload.get("modeling_plan_id")
                        if plan_id and (plan := await self.session.get(ModelingPlan, uuid.UUID(plan_id))):
                            plan.status = "completed"
                if mjob.status in ("failed", "cancelled"):
                    metadata.setdefault("error_code", mjob.diagnostics.get("error_code"))
        if body.intent in ("export", "render") and body.success:
            await self._start_output(job, body, metadata)
        if body.error_code:
            metadata["error_code"] = body.error_code

        cancelled = body.error_code == "cancelled"
        reply = body.reply or (body.error_message or "Something went wrong.")
        conversation_id = uuid.UUID(job.payload["conversation_id"])
        message = Message(
            conversation_id=conversation_id,
            created_at=await self._next_timestamp(conversation_id),
            role="assistant",
            content=reply[:4000],
            intent=body.intent,
            status="complete" if body.success else ("cancelled" if cancelled else "failed"),
            metadata_=metadata,
        )
        self.session.add(message)
        now = utcnow()
        job.status = "succeeded" if body.success else ("cancelled" if cancelled else "failed")
        job.completed_at = now
        job.error_message = None if body.success else (body.error_message or reply)[:1000]
        job.diagnostics = {**job.diagnostics, "error_code": body.error_code}
        job.progress = {"stage": job.status, "message": reply[:200]}
        if version_number:
            job.payload = {**job.payload, "result_version_id": metadata["version_id"]}
        await self.session.flush()
        await self.session.commit()
        await self.redis.delete(cancel_flag_key(job.id))
        event_data = {
            "message_id": str(message.id),
            "intent": body.intent,
            **{k: metadata[k] for k in ("version_id", "version_number", "error_code") if metadata.get(k) is not None},
        }
        if body.success:
            await self.events.publish(job.id, "job.completed", stage="done", message=reply[:300], data=event_data)
        else:
            await self.events.publish(
                job.id,
                "job.cancelled" if cancelled else "job.failed",
                stage=job.status,
                message=reply[:300],
                data=event_data,
            )
        return message

    async def _start_output(self, job: Job, body: AgentCompleteRequest, metadata: dict[str, Any]) -> None:
        """ "Export it as STL" / "render a picture": start the export or render job for the version the
        user was looking at; the chat message then tracks it (download button / image)."""
        metadata["action"] = body.intent
        project = await self.session.get(Project, job.project_id)
        head = (
            await self.session.get(SceneVersion, project.current_version_id)
            if project and project.current_version_id
            else None
        )
        if project is None or head is None:
            metadata["output_error"] = "There is no model to export yet."
            return
        # pin the output to what the user saw when they asked
        metadata.update(export_version_id=str(head.id), export_version_number=head.version_number)
        svc = OutputService(self.session, self.redis, self.settings, ProjectStorage(self.settings.data_root))
        try:
            if body.intent == "export":
                fmt = body.metadata.get("export_format")
                fmt = fmt if fmt in DOWNLOAD_FORMATS else "glb"
                metadata["export_format"] = fmt
                out = await svc.create_export(project.user_id, project.id, fmt, version_id=head.id)
            else:
                quality = body.metadata.get("render_quality")
                quality = quality if quality in ("draft", "standard", "high") else "standard"
                metadata["render_quality"] = quality
                out = await svc.create_render(
                    project.user_id, project.id, RenderSettings(quality=quality, width=1280, height=720), head.id
                )
            metadata["output_job_id"] = str(out.id)
        except AppError as exc:
            metadata["output_error"] = exc.message

    async def batch_for(self, batch_id: uuid.UUID) -> OperationBatch | None:
        return await self.session.get(OperationBatch, batch_id)

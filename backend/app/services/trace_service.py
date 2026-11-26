"""Agent trace of one turn ("How this was made"): the LangGraph path the turn took, what each step decided,
and the LLM calls inside each step. Owners see summaries; administrators also get the LLM prompts."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models import AgentRun, Job, Message, Project, User
from forma3d_shared.schemas.agent import AGENT_GRAPH_KEY

TERMINAL = {"succeeded", "failed", "cancelled"}


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _ms(start: datetime | None, end: datetime | None) -> int | None:
    if not start or not end:
        return None
    try:
        return max(0, int((end - start).total_seconds() * 1000))
    except TypeError:  # naive vs aware (SQLite)
        return max(0, int((end.replace(tzinfo=None) - start.replace(tzinfo=None)).total_seconds() * 1000))


async def agent_graph(redis: Redis | None) -> dict[str, Any] | None:
    if redis is None:
        return None
    try:
        raw = await redis.get(AGENT_GRAPH_KEY)
    except Exception:
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _kind(run: AgentRun) -> str:
    """Rows recorded before step tracing have kind `agent`; the ones with a real model were LLM calls."""
    if run.kind == "agent" and run.model_name and run.model_name != "deterministic":
        return "llm"
    return run.kind


def _call(run: AgentRun, include_debug: bool) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(run.id),
        "kind": _kind(run),
        "agent": run.agent_name,
        "model": run.model_name if run.model_name != "deterministic" else None,
        "status": run.status,
        "duration_ms": run.duration_ms,
        "input": run.input_metadata or {},
        "output": run.output_metadata or {},
        "error": run.error_message,
        "created_at": _iso(run.created_at),
    }
    if include_debug:
        out["debug"] = run.debug
    return out


def build_trace(job: Job, runs: list[AgentRun], message: Message | None, graph: dict | None, *, include_debug: bool):
    finished = job.status in TERMINAL
    steps: dict[uuid.UUID, dict[str, Any]] = {}
    seq_index: dict[int, dict[str, Any]] = {}
    for run in runs:
        if run.kind != "node":
            continue
        status = run.status
        if status == "running" and finished:
            status = "interrupted"  # the worker died or the job ended while this step was running
        step = {
            "id": str(run.id),
            "node": run.node or run.agent_name,
            "seq": run.seq or 0,
            "status": status,
            "duration_ms": run.duration_ms,
            "started_at": _iso(run.created_at),
            "input": run.input_metadata or {},
            "output": run.output_metadata or {},
            "error": run.error_message,
            "calls": [],
        }
        steps[run.id] = step
        seq_index[step["seq"]] = step
    loose: list[dict[str, Any]] = []
    for run in runs:
        if run.kind == "node":
            continue
        owner = seq_index.get(run.seq) if run.seq is not None else None
        (owner["calls"] if owner else loose).append(_call(run, include_debug))
    ordered = sorted(steps.values(), key=lambda s: (s["seq"], s["started_at"] or ""))

    llm = [c for s in ordered for c in s["calls"] if c["kind"] == "llm"] + [c for c in loose if c["kind"] == "llm"]
    totals = {
        "steps": len(ordered),
        "llm_calls": len(llm),
        "llm_ms": sum(c["duration_ms"] or 0 for c in llm),
        "prompt_tokens": sum(int(c["input"].get("prompt_tokens") or 0) for c in llm),
        "completion_tokens": sum(int(c["output"].get("completion_tokens") or 0) for c in llm),
        "revisions": sum(1 for s in ordered if s["node"] == "revise"),
    }
    return {
        "job": {
            "id": str(job.id),
            "project_id": str(job.project_id),
            "status": job.status,
            "created_at": _iso(job.created_at),
            "started_at": _iso(job.started_at),
            "completed_at": _iso(job.completed_at),
            "duration_ms": _ms(job.started_at, job.completed_at),
            "error_message": job.error_message,
        },
        "message": {"id": str(message.id), "content": message.content[:500]} if message else None,
        "graph": graph,
        "steps": ordered,
        "calls": loose,  # rows recorded before step tracing existed
        "totals": totals,
        "include_debug": include_debug,
    }


class TraceService:
    def __init__(self, session: AsyncSession, redis: Redis | None) -> None:
        self.session = session
        self.redis = redis

    async def _job(self, job_id: uuid.UUID, *, user_id: uuid.UUID | None, project_id: uuid.UUID | None) -> Job:
        stmt = select(Job).join(Project, Project.id == Job.project_id).where(Job.id == job_id, Job.job_type == "agent")
        if user_id is not None:
            stmt = stmt.where(Project.user_id == user_id, Project.status != "deleted")
        if project_id is not None:
            stmt = stmt.where(Job.project_id == project_id)
        job = (await self.session.execute(stmt)).scalar_one_or_none()
        if job is None:
            raise NotFoundError("Agent run not found")
        return job

    async def trace(
        self,
        job_id: uuid.UUID,
        *,
        user_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        include_debug: bool = False,
    ) -> dict[str, Any]:
        job = await self._job(job_id, user_id=user_id, project_id=project_id)
        runs = list(
            (
                await self.session.execute(
                    select(AgentRun).where(AgentRun.job_id == job.id).order_by(AgentRun.created_at, AgentRun.seq)
                )
            ).scalars()
        )
        message_id = (job.payload or {}).get("message_id")
        message = await self.session.get(Message, uuid.UUID(message_id)) if message_id else None
        return build_trace(job, runs, message, await agent_graph(self.redis), include_debug=include_debug)

    async def recent(self, *, q: str = "", status: str = "", limit: int = 50, offset: int = 0) -> dict[str, Any]:
        """Admin: recent agent turns of all users."""
        where = [Job.job_type == "agent"]
        if status:
            where.append(Job.status == status)
        if q.strip():
            like = f"%{q.strip().lower()}%"
            where.append(func.lower(User.email).like(like) | func.lower(Project.name).like(like))
        base = (
            select(Job, Project, User)
            .join(Project, Project.id == Job.project_id)
            .join(User, User.id == Project.user_id)
        )
        total = (
            await self.session.execute(
                select(func.count(Job.id))
                .select_from(Job)
                .join(Project, Project.id == Job.project_id)
                .join(User, User.id == Project.user_id)
                .where(*where)
            )
        ).scalar_one()
        rows = (
            await self.session.execute(base.where(*where).order_by(Job.created_at.desc()).limit(limit).offset(offset))
        ).all()
        job_ids = [job.id for job, _p, _u in rows]
        message_ids = [uuid.UUID(m) for job, _p, _u in rows if (m := (job.payload or {}).get("message_id"))]
        messages = (
            {
                m.id: m
                for m in (await self.session.execute(select(Message).where(Message.id.in_(message_ids)))).scalars()
            }
            if message_ids
            else {}
        )
        stats: dict[uuid.UUID, dict[str, Any]] = {
            jid: {"llm_calls": 0, "tokens": 0, "revisions": 0, "steps": 0} for jid in job_ids
        }
        if job_ids:
            for run in (await self.session.execute(select(AgentRun).where(AgentRun.job_id.in_(job_ids)))).scalars():
                s = stats[run.job_id]  # type: ignore[index]
                if _kind(run) == "llm":
                    s["llm_calls"] += 1
                    s["tokens"] += int((run.input_metadata or {}).get("prompt_tokens") or 0) + int(
                        (run.output_metadata or {}).get("completion_tokens") or 0
                    )
                elif run.kind == "node" and run.status != "running":
                    s["steps"] += 1
                    s["revisions"] += run.node == "revise"
        items = []
        for job, project, user in rows:
            m = messages.get(uuid.UUID(mid)) if (mid := (job.payload or {}).get("message_id")) else None
            items.append(
                {
                    "job_id": str(job.id),
                    "project_id": str(project.id),
                    "project_name": project.name,
                    "user_email": user.email,
                    "message": (m.content[:200] if m else ""),
                    "status": job.status,
                    "created_at": _iso(job.created_at),
                    "duration_ms": _ms(job.started_at, job.completed_at),
                    **stats[job.id],
                }
            )
        return {"items": items, "total": int(total)}

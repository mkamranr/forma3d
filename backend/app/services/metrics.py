"""Prometheus text-format metrics (spec §16), without a client library (air-gapped, pinned deps).

Two kinds of series:

- **process-local**: HTTP request counts/latency, recorded by the request middleware. With several
  backend replicas Prometheus scrapes each and sums by instance.
- **cluster-wide gauges**, computed at scrape time from PostgreSQL / Redis / the data volume: jobs by
  type and status (cumulative, so `rate()` works), recent job durations, queue depth and pending
  (in-flight) entries per stream, live workers, LLM reachability as seen by the agent workers, disk.
"""

from __future__ import annotations

import asyncio
import shutil
import threading
from collections import defaultdict
from datetime import timedelta
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utcnow
from app.models import Job, Project, User
from forma3d_shared.schemas.agent import AGENT_JOB_GROUP, AGENT_JOB_STREAM
from forma3d_shared.schemas.job import BLENDER_JOB_GROUP, BLENDER_JOB_STREAM

BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)


def _labels(labels: dict[str, Any]) -> str:
    if not labels:
        return ""
    inner = ",".join(
        f'{k}="{str(v).replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))}"' for k, v in labels.items()
    )
    return "{" + inner + "}"


class HttpMetrics:
    """Request counter + latency histogram keyed by (method, route template, status class)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.count: dict[tuple[str, str, str], int] = defaultdict(int)
        self.buckets: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0] * (len(BUCKETS) + 1))
        self.sum: dict[tuple[str, str], float] = defaultdict(float)

    def observe(self, method: str, route: str, status: int, seconds: float) -> None:
        with self._lock:
            self.count[(method, route, f"{status // 100}xx")] += 1
            key = (method, route)
            b = self.buckets[key]
            for i, edge in enumerate(BUCKETS):
                if seconds <= edge:
                    b[i] += 1
            b[-1] += 1  # +Inf
            self.sum[key] += seconds

    def render(self) -> list[str]:
        out = [
            "# HELP forma3d_http_requests_total HTTP requests by route template and status class.",
            "# TYPE forma3d_http_requests_total counter",
        ]
        with self._lock:
            for (method, route, status), n in sorted(self.count.items()):
                out.append(
                    f"forma3d_http_requests_total{_labels({'method': method, 'route': route, 'status': status})} {n}"
                )
            out += [
                "# HELP forma3d_http_request_duration_seconds HTTP request latency (SSE streams included).",
                "# TYPE forma3d_http_request_duration_seconds histogram",
            ]
            for (method, route), b in sorted(self.buckets.items()):
                base = {"method": method, "route": route}
                for edge, n in zip((*BUCKETS, "+Inf"), b, strict=True):
                    out.append(f"forma3d_http_request_duration_seconds_bucket{_labels({**base, 'le': edge})} {n}")
                out.append(f"forma3d_http_request_duration_seconds_sum{_labels(base)} {self.sum[(method, route)]:.6f}")
                out.append(f"forma3d_http_request_duration_seconds_count{_labels(base)} {b[-1]}")
        return out


HTTP = HttpMetrics()


def _s(v: Any) -> Any:
    return v.decode() if isinstance(v, bytes) else v


def _get(info: dict | None, key: str) -> Any:
    if not info:
        return None
    v = info.get(key, info.get(key.encode()))
    return int(v) if isinstance(v, (int, bytes, str)) and str(_s(v)).lstrip("-").isdigit() else None


def _gauge(
    out: list[str], name: str, help_: str, rows: list[tuple[dict[str, Any], float]], kind: str = "gauge"
) -> None:
    out.append(f"# HELP {name} {help_}")
    out.append(f"# TYPE {name} {kind}")
    for labels, value in rows:
        out.append(f"{name}{_labels(labels)} {value:g}")


async def _scan_count(redis: Redis, pattern: str) -> list[str]:
    keys = []
    async for key in redis.scan_iter(match=pattern, count=500):
        keys.append(key.decode() if isinstance(key, bytes) else key)
    return keys


async def collect(session: AsyncSession, redis: Redis | None, data_root: str) -> str:
    out = HTTP.render()

    jobs = (
        await session.execute(select(Job.job_type, Job.status, func.count()).group_by(Job.job_type, Job.status))
    ).all()
    _gauge(
        out,
        "forma3d_jobs",
        "Jobs by type and status (cumulative for finished statuses).",
        [({"type": t, "status": s}, n) for t, s, n in jobs],
    )
    since = utcnow() - timedelta(minutes=15)
    recent = (
        await session.execute(
            select(Job.job_type, Job.status, Job.started_at, Job.completed_at).where(
                Job.completed_at.is_not(None), Job.completed_at >= since, Job.started_at.is_not(None)
            )
        )
    ).all()
    durations: dict[tuple[str, str], list[float]] = defaultdict(list)
    for t, s, started, completed in recent:
        durations[(t, s)].append(max(0.0, (completed - started).total_seconds()))
    _gauge(
        out,
        "forma3d_job_duration_seconds_avg_15m",
        "Average duration of jobs finished in the last 15 minutes.",
        [({"type": t, "status": s}, sum(v) / len(v)) for (t, s), v in sorted(durations.items())],
    )
    _gauge(
        out,
        "forma3d_job_duration_seconds_max_15m",
        "Longest job finished in the last 15 minutes.",
        [({"type": t, "status": s}, max(v)) for (t, s), v in sorted(durations.items())],
    )
    users = (await session.execute(select(func.count(User.id)))).scalar_one()
    projects = (await session.execute(select(Project.status, func.count()).group_by(Project.status))).all()
    _gauge(out, "forma3d_users", "Registered users.", [({}, users)])
    _gauge(out, "forma3d_projects", "Projects by status.", [({"status": s}, n) for s, n in projects])

    if redis is not None:
        queue_rows, pending_rows = [], []
        for stream, group, name in (
            (BLENDER_JOB_STREAM, BLENDER_JOB_GROUP, "blender"),
            (AGENT_JOB_STREAM, AGENT_JOB_GROUP, "agent"),
        ):
            try:
                length = int(await redis.xlen(stream))
                groups = await redis.xinfo_groups(stream) if length else []
            except Exception:  # stream not created yet
                length, groups = 0, []
            info = next((g for g in groups if _s(g.get("name") or g.get(b"name")) == group), None)
            lag = _get(info, "lag")
            pending = _get(info, "pending") or 0
            # no consumer group yet (no worker ever started): everything in the stream is waiting
            waiting = length if info is None else (lag if lag is not None else max(0, length - pending))
            queue_rows.append(({"queue": name}, float(waiting)))
            pending_rows.append(({"queue": name}, float(pending or 0)))
        _gauge(out, "forma3d_queue_waiting", "Jobs waiting in the stream (not yet delivered to a worker).", queue_rows)
        _gauge(out, "forma3d_queue_in_flight", "Jobs delivered to a worker and not yet acknowledged.", pending_rows)
        blender = await _scan_count(redis, "workers:blender:*")
        agent = [k for k in await _scan_count(redis, "workers:agent:*") if not k.endswith(":llm")]
        _gauge(
            out,
            "forma3d_workers_alive",
            "Workers that sent a heartbeat in the last 45 seconds.",
            [({"kind": "blender"}, len(blender)), ({"kind": "agent"}, len(agent))],
        )
        llm = await llm_status(redis)
        _gauge(
            out,
            "forma3d_llm_reachable",
            "1 when at least one agent worker reached the LLM endpoint recently (-1: unknown).",
            [({}, {"ok": 1, "unavailable": 0}.get(llm, -1))],
        )

    usage = await asyncio.to_thread(_disk, data_root)
    if usage:
        _gauge(
            out,
            "forma3d_data_disk_bytes",
            "Project data volume size.",
            [({"kind": "total"}, usage[0]), ({"kind": "free"}, usage[1])],
        )
    return "\n".join(out) + "\n"


def _disk(path: str) -> tuple[int, int] | None:
    try:
        u = shutil.disk_usage(path)
        return u.total, u.free
    except OSError:
        return None


async def llm_status(redis: Redis) -> str:
    """'ok' if any agent worker reached the LLM recently, 'unavailable' if they all failed, else 'unknown'."""
    seen = []
    async for key in redis.scan_iter(match="workers:agent:*:llm", count=200):
        value = await redis.get(key)
        seen.append(value.decode() if isinstance(value, bytes) else value)
    if "ok" in seen:
        return "ok"
    return "unavailable" if seen else "unknown"


def disk_free_ratio(path: str) -> float | None:
    usage = _disk(path)
    return usage[1] / usage[0] if usage and usage[0] else None

"""Phase 8: job recovery, stale locks, retention, metrics, readiness and secrets."""

from __future__ import annotations

import re
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from httpx import AsyncClient
from pydantic import ValidationError

from app.core.config import Settings
from app.core.time import utcnow
from app.services.maintenance import TIMEOUT_REPLY, Maintenance
from app.services.storage import ProjectStorage
from forma3d_shared.examples import black_metal_legs_batch, desk_batch
from forma3d_shared.secrets import env_secret, file_secret, with_password
from tests.test_modeling_pipeline import INTERNAL, FakeWorker, _setup, _submit, settings  # noqa: F401
from tests.test_outputs import _finish, _spec


def maint(client: AsyncClient, settings: Settings) -> Maintenance:  # noqa: F811
    app = client.app  # type: ignore[attr-defined]
    return Maintenance(app.state.db.sessionmaker, app.state.redis, settings, ProjectStorage(settings.data_root))


async def job(client: AsyncClient, h: dict, job_id: str) -> dict:
    return (await client.get(f"/api/v1/jobs/{job_id}", headers=h)).json()


async def test_stuck_running_job_is_failed_and_unlocks_the_project(client: AsyncClient, settings: Settings) -> None:  # noqa: F811
    h, pid = await _setup(client)
    jid = (await _submit(client, h, pid, desk_batch()))["job_id"]
    res = await client.post(f"/internal/v1/jobs/{jid}/start", json={"worker_id": "blender-dead"}, headers=INTERNAL)
    assert res.json()["proceed"]
    m = maint(client, settings)

    assert (await m.sweep_jobs())["timed_out"] == 0  # still within its timeout
    later = utcnow() + timedelta(seconds=settings.job_timeout_seconds + settings.job_grace_seconds + 5)
    stats = await m.run_once(later)
    assert stats["timed_out"] == 1
    j = await job(client, h, jid)
    assert j["status"] == "failed" and "did not finish in time" in j["error_message"]
    # the project accepts work again
    await _submit(client, h, pid, desk_batch())


async def test_job_nobody_picked_up_fails_after_the_queue_timeout(client: AsyncClient, settings: Settings) -> None:  # noqa: F811
    h, pid = await _setup(client)
    jid = (await _submit(client, h, pid, desk_batch()))["job_id"]
    m = maint(client, settings)
    await m.run_once(utcnow() + timedelta(seconds=settings.job_timeout_seconds + settings.job_grace_seconds + 5))
    assert (await job(client, h, jid))["status"] == "queued"  # queued jobs get longer (workers may restart)
    await m.run_once(utcnow() + timedelta(seconds=settings.queued_job_timeout_seconds + 5))
    j = await job(client, h, jid)
    assert j["status"] == "failed" and "No worker picked up" in j["error_message"]


async def test_stuck_agent_turn_gets_a_failed_reply(client: AsyncClient, settings: Settings) -> None:  # noqa: F811
    h, pid = await _setup(client)
    res = await client.post(f"/api/v1/projects/{pid}/conversation/messages", headers=h, json={"content": "make a desk"})
    jid = res.json()["job_id"]
    await client.post(
        f"/internal/v1/jobs/{jid}/start", json={"worker_id": "agent-dead", "reclaim": False}, headers=INTERNAL
    )
    stats = await maint(client, settings).run_once(utcnow() + timedelta(seconds=settings.agent_job_timeout_seconds + 5))
    assert stats["agent_timed_out"] == 1
    messages = (await client.get(f"/api/v1/projects/{pid}/conversation", headers=h)).json()["messages"]
    assert messages[-1]["role"] == "assistant" and messages[-1]["status"] == "failed"
    assert messages[-1]["content"] == TIMEOUT_REPLY
    # chat is usable again
    res = await client.post(f"/api/v1/projects/{pid}/conversation/messages", headers=h, json={"content": "again"})
    assert res.status_code == 202


async def test_reclaimed_blender_job_reruns_only_when_its_worker_is_dead(
    client: AsyncClient,
    settings: Settings,  # noqa: F811
) -> None:
    h, pid = await _setup(client)
    jid = (await _submit(client, h, pid, desk_batch()))["job_id"]
    redis = client.app.state.redis  # type: ignore[attr-defined]
    await client.post(f"/internal/v1/jobs/{jid}/start", json={"worker_id": "blender-a"}, headers=INTERNAL)
    await redis.set("workers:blender:blender-a", 1, ex=45)

    res = await client.post(
        f"/internal/v1/jobs/{jid}/start", json={"worker_id": "blender-b", "reclaim": True}, headers=INTERNAL
    )
    assert res.json() == {"proceed": False, "reason": "running_elsewhere"}
    # a plain duplicate delivery is still refused
    res = await client.post(f"/internal/v1/jobs/{jid}/start", json={"worker_id": "blender-b"}, headers=INTERNAL)
    assert res.json()["proceed"] is False

    await redis.delete("workers:blender:blender-a")  # worker A died
    res = await client.post(
        f"/internal/v1/jobs/{jid}/start", json={"worker_id": "blender-b", "reclaim": True}, headers=INTERNAL
    )
    assert res.json() == {"proceed": True, "reason": "rerun"}
    j = await job(client, h, jid)
    assert j["status"] == "running"


async def test_stale_lock_of_a_finished_job_is_released(client: AsyncClient, settings: Settings) -> None:  # noqa: F811
    h, pid = await _setup(client)
    jid = (await _submit(client, h, pid, desk_batch()))["job_id"]
    await FakeWorker(client, settings.data_root).run()
    redis = client.app.state.redis  # type: ignore[attr-defined]
    await redis.set(f"lock:project:{pid}", jid, ex=600)  # leaked by a crash after completion
    legs = black_metal_legs_batch()
    res = await client.post(f"/api/v1/projects/{pid}/modeling/execute", json=legs, headers=h)
    assert res.status_code == 409
    assert await maint(client, settings).release_stale_locks() == 1
    assert (await client.post(f"/api/v1/projects/{pid}/modeling/execute", json=legs, headers=h)).status_code == 202


async def test_deleted_projects_and_old_outputs_are_purged(client: AsyncClient, settings: Settings) -> None:  # noqa: F811
    h, pid = await _setup(client)
    await _submit(client, h, pid, desk_batch())
    await FakeWorker(client, settings.data_root).run()

    # an STL export, then retention
    res = await client.post(f"/api/v1/projects/{pid}/exports", json={"format": "stl"}, headers=h)
    eid = res.json()["id"]
    spec = await _spec(client)
    await _finish(client, settings, spec, "scene.stl", b"\0" * 80 + (0).to_bytes(4, "little"))
    out = (await client.get(f"/api/v1/exports/{eid}", headers=h)).json()
    assert out["url"]
    m = maint(client, settings)
    assert await m.purge_old_outputs() == 0
    assert await m.purge_old_outputs(utcnow() + timedelta(days=settings.output_retention_days + 1)) == 1
    out = (await client.get(f"/api/v1/exports/{eid}", headers=h)).json()
    assert out["status"] == "succeeded" and out["url"] is None  # the UI shows "expired"
    versions = (await client.get(f"/api/v1/projects/{pid}/versions", headers=h)).json()
    assert len(versions) == 1  # versions are never purged

    # soft delete -> purge after the retention period (rows cascade, files removed)
    me = (await client.get("/api/v1/auth/me", headers=h)).json()
    project_dir = next(Path(settings.data_root, "projects", me["id"]).iterdir())
    assert project_dir.is_dir()
    assert (await client.delete(f"/api/v1/projects/{pid}", headers=h)).status_code == 204
    assert await m.purge_deleted_projects() == 0
    assert await m.purge_deleted_projects(utcnow() + timedelta(days=settings.deleted_project_retention_days + 1)) == 1
    assert not project_dir.exists()
    async with client.app.state.db.sessionmaker() as session:  # type: ignore[attr-defined]
        from sqlalchemy import func, select

        from app.models import Job, SceneVersion

        assert (await session.execute(select(func.count(SceneVersion.id)))).scalar_one() == 0
        assert (await session.execute(select(func.count(Job.id)))).scalar_one() == 0


async def test_only_one_sweeper_leads(client: AsyncClient, settings: Settings) -> None:  # noqa: F811
    a, b = maint(client, settings), maint(client, settings)
    await client.app.state.redis.delete("maintenance:leader")  # type: ignore[attr-defined]
    assert await a.is_leader("a") is True
    assert await b.is_leader("b") is False
    assert await a.is_leader("a") is True  # renews


async def test_metrics_and_readiness(client: AsyncClient, settings: Settings) -> None:  # noqa: F811
    h, pid = await _setup(client)
    await _submit(client, h, pid, desk_batch())
    await client.app.state.redis.set("workers:blender:b1", 1, ex=45)  # type: ignore[attr-defined]
    await client.app.state.redis.set("workers:agent:a1:llm", "ok", ex=120)  # type: ignore[attr-defined]

    res = await client.get("/metrics")
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/plain")
    body = res.text
    assert 'forma3d_jobs{type="modeling",status="queued"} 1' in body
    # process-wide counter (other tests in the run add to it): the series exists with a route *template*
    execute = r'route="/api/v1/projects/\{project_id\}/modeling/execute",status="2xx"\} [1-9]'
    assert re.search(r'forma3d_http_requests_total\{method="POST",' + execute, body)
    assert 'forma3d_http_request_duration_seconds_bucket{method="POST",route="/api/v1/auth/register",le="+Inf"}' in body
    assert 'forma3d_queue_waiting{queue="blender"} 1' in body
    assert 'forma3d_workers_alive{kind="blender"} 1' in body
    assert "forma3d_llm_reachable 1" in body
    assert pid not in body  # route templates, never raw ids

    ready = (await client.get("/health/ready")).json()
    assert ready["checks"] == {"database": "ok", "redis": "ok"}
    assert ready["info"]["blender_workers"] == "1" and ready["info"]["agent_workers"] == "0"
    assert ready["status"] == "degraded"  # no agent worker

    client.app.state.settings.metrics_token = "scrape-token"  # type: ignore[attr-defined]
    try:
        assert (await client.get("/metrics")).status_code == 401
        assert (await client.get("/metrics", headers={"Authorization": "Bearer scrape-token"})).status_code == 200
    finally:
        client.app.state.settings.metrics_token = ""  # type: ignore[attr-defined]


def test_secrets_from_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "jwt_secret").write_text("from-a-mounted-secret-0123456789\n")
    (tmp_path / "db").write_text("p@ss:w/rd")
    monkeypatch.setenv("SECRETS_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_PASSWORD_FILE", str(tmp_path / "db"))
    s = Settings(database_url="postgresql+asyncpg://forma3d@postgres:5432/forma3d")
    assert s.jwt_secret == "from-a-mounted-secret-0123456789"
    assert s.database_url == "postgresql+asyncpg://forma3d:p%40ss%3Aw%2Frd@postgres:5432/forma3d"

    assert env_secret("NOPE", "dflt", {}) == "dflt"
    assert env_secret("X", environ={"X": "plain"}) == "plain"
    assert file_secret("X", {"X": "plain", "SECRETS_DIR": str(tmp_path)}) == ""  # files only
    monkeypatch.setenv("BROKEN_FILE", str(tmp_path / "missing"))
    with pytest.raises(RuntimeError):
        env_secret("BROKEN")
    assert with_password("sqlite+aiosqlite:///x.db", "p") == "sqlite+aiosqlite:///x.db"
    assert with_password("redis://redis:6379/0", "p") == "redis://redis:6379/0"  # no user: unchanged


def test_production_refuses_default_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRETS_DIR", "/nonexistent")
    with pytest.raises(ValidationError, match="strong secrets"):
        Settings(app_env="production", jwt_secret="change-me-to-a-long-random-string")
    ok = Settings(app_env="production", jwt_secret=uuid.uuid4().hex * 2, internal_service_token=uuid.uuid4().hex)
    assert ok.is_production

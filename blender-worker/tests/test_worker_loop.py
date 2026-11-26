"""Queue semantics with fakeredis and a fake backend (Blender execution stubbed)."""

from __future__ import annotations

import fakeredis
import pytest
from forma3d_shared.examples import desk_batch
from forma3d_shared.schemas.events import event_stream_key
from forma3d_shared.schemas.job import BLENDER_JOB_GROUP, BLENDER_JOB_STREAM, BlenderJobResult, JobStartResponse

from app import worker as worker_mod
from app.backend_client import BackendUnavailable
from app.worker import Worker
from tests.conftest import canonical, make_spec


class FakeBackend:
    def __init__(self, proceed=True, fail_complete=False, reason="already finished"):
        self.proceed = proceed
        self.fail_complete = fail_complete
        self.reason = reason
        self.started, self.completed, self.reclaims = [], [], []

    def start(self, job_id, worker_id, *, reclaim=False):
        self.started.append(job_id)
        self.reclaims.append(reclaim)
        return JobStartResponse(proceed=self.proceed, reason="" if self.proceed else self.reason)

    def complete(self, result):
        if self.fail_complete:
            raise BackendUnavailable("down")
        self.completed.append(result)
        return {}


@pytest.fixture
def r():
    return fakeredis.FakeRedis(decode_responses=True)


def enqueue(r, spec):
    return r.xadd(BLENDER_JOB_STREAM, {"job": spec.model_dump_json()})


def pending(r) -> int:
    return r.xpending(BLENDER_JOB_STREAM, BLENDER_JOB_GROUP)["pending"]


def read_one(w: Worker):
    batches = w.redis.xreadgroup(BLENDER_JOB_GROUP, w.cfg.worker_id, {BLENDER_JOB_STREAM: ">"}, count=1)
    return batches[0][1][0]


def fake_execute(success=True):
    def _run(spec, cfg, emit, is_cancelled):
        emit("executing", "Executing", {})
        emit("operation", "create_cube", {"index": 1, "total": 1})
        return BlenderJobResult(job_id=spec.job_id, success=success, error_code=None if success else "boom")

    return _run


def test_successful_job_is_reported_and_acked(cfg, r, monkeypatch):
    monkeypatch.setattr(worker_mod, "execute_job", fake_execute())
    backend = FakeBackend()
    w = Worker(cfg, r, backend)
    w.ensure_group()
    spec = make_spec(canonical(desk_batch()))
    enqueue(r, spec)
    w._safe_handle(*read_one(w))
    assert backend.completed[0].success
    assert pending(r) == 0
    events = [e[1]["event"] for e in r.xrange(event_stream_key(spec.job_id))]
    assert any('"blender.started"' in e for e in events)
    assert any('"blender.progress"' in e and "Applying create_cube" in e for e in events)
    assert any('"blender.completed"' in e for e in events)


def test_duplicate_or_cancelled_job_is_dropped(cfg, r, monkeypatch):
    called = []
    monkeypatch.setattr(worker_mod, "execute_job", lambda *a, **k: called.append(1))
    w = Worker(cfg, r, FakeBackend(proceed=False))
    w.ensure_group()
    enqueue(r, make_spec(canonical(desk_batch())))
    w._safe_handle(*read_one(w))
    assert called == [] and pending(r) == 0


def test_backend_down_leaves_message_pending_for_retry(cfg, r, monkeypatch):
    monkeypatch.setattr(worker_mod, "execute_job", fake_execute())
    w = Worker(cfg, r, FakeBackend(fail_complete=True))
    w.ensure_group()
    enqueue(r, make_spec(canonical(desk_batch())))
    w._safe_handle(*read_one(w))
    assert pending(r) == 1


def test_garbage_message_is_acked_not_retried_forever(cfg, r):
    w = Worker(cfg, r, FakeBackend())
    w.ensure_group()
    r.xadd(BLENDER_JOB_STREAM, {"job": "{not json"})
    w._safe_handle(*read_one(w))
    assert pending(r) == 0


def test_ensure_group_is_idempotent(cfg, r):
    w = Worker(cfg, r, FakeBackend())
    w.ensure_group()
    w.ensure_group()


def test_reclaim_reruns_a_dead_workers_job_and_respects_a_live_owner(cfg, r, monkeypatch):
    monkeypatch.setattr(worker_mod, "execute_job", fake_execute())
    dead = Worker(cfg, r, FakeBackend())
    dead.ensure_group()
    enqueue(r, make_spec(canonical(desk_batch())))
    read_one(dead)  # delivered to a worker that then crashed: pending, never acked
    assert pending(r) == 1

    from dataclasses import replace

    live_backend = FakeBackend(proceed=False, reason="running_elsewhere")
    other = Worker(replace(cfg, worker_id="blender-other", reclaim_idle_seconds=30), r, live_backend)
    object.__setattr__(other.cfg, "reclaim_idle_seconds", 0)  # frozen dataclass: test-only override
    other.reclaim_pending()
    assert live_backend.reclaims == [True] and pending(r) == 1  # owner still alive: left pending

    backend = FakeBackend()
    taker = Worker(replace(cfg, worker_id="blender-taker"), r, backend)
    object.__setattr__(taker.cfg, "reclaim_idle_seconds", 0)
    taker.reclaim_pending()
    assert backend.reclaims == [True] and len(backend.completed) == 1 and pending(r) == 0


def test_heartbeat_keeps_the_in_flight_entry_fresh(cfg, r):
    w = Worker(cfg, r, FakeBackend())
    w.ensure_group()
    enqueue(r, make_spec(canonical(desk_batch())))
    msg_id, _ = read_one(w)
    w.current_msg = msg_id
    import threading

    t = threading.Thread(target=w.heartbeat_loop, daemon=True)
    t.start()
    import time

    time.sleep(0.2)
    w.stop.set()
    t.join(2)
    entry = r.xpending_range(BLENDER_JOB_STREAM, BLENDER_JOB_GROUP, min="-", max="+", count=1)[0]
    assert entry["consumer"] == cfg.worker_id and entry["time_since_delivered"] < 1000
    assert r.get(f"workers:blender:{cfg.worker_id}")


def test_blender_never_sees_worker_secrets(tmp_path):
    from app.job_runner import blender_env

    env = blender_env(
        tmp_path,
        {
            "PATH": "/usr/bin",
            "INTERNAL_SERVICE_TOKEN": "s3cret",
            "REDIS_URL": "redis://r",
            "F3D_CYCLES_DEVICE": "OPTIX",
        },
    )
    assert env["PATH"] == "/usr/bin" and env["F3D_CYCLES_DEVICE"] == "OPTIX"
    assert "INTERNAL_SERVICE_TOKEN" not in env and "REDIS_URL" not in env
    assert env["BLENDER_USER_CONFIG"].startswith(str(tmp_path))


def test_limits_changed_in_the_app_apply_before_the_next_job(cfg, r):
    class ConfigBackend(FakeBackend):
        values: dict = {}

        def runtime_config(self):
            return {"version": 1, "values": self.values}

    backend = ConfigBackend()
    w = Worker(cfg, r, backend)
    default = w.cfg.limits.max_scene_vertices
    w.refresh_limits()
    assert w.cfg.limits.max_scene_vertices == default
    backend.values = {"modeling_max_scene_vertices": 5_000_000, "llm_model": "ignored-here"}
    w.refresh_limits()  # version unchanged: not re-read
    assert w.cfg.limits.max_scene_vertices == default
    r.incr("config:version")
    w.refresh_limits()
    assert w.cfg.limits.max_scene_vertices == 5_000_000

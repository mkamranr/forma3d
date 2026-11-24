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
    def __init__(self, proceed=True, fail_complete=False):
        self.proceed = proceed
        self.fail_complete = fail_complete
        self.started, self.completed = [], []

    def start(self, job_id, worker_id):
        self.started.append(job_id)
        return JobStartResponse(proceed=self.proceed, reason="" if self.proceed else "already finished")

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

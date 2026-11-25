"""Runner + worker loop against fakeredis and an in-memory fake backend."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import fakeredis
from forma3d_shared.schemas.agent import AGENT_JOB_STREAM, AgentCompleteRequest, AgentContext, AgentJobSpec
from forma3d_shared.schemas.events import JobEvent, cancel_flag_key, event_stream_key
from forma3d_shared.schemas.job import JobStartResponse
from forma3d_shared.schemas.scene_object import SceneInventory

from app.backend_client import BackendRejected
from app.config import AgentConfig
from app.planner import Planner
from app.providers.fake import FakeProvider
from app.runner import AgentRunner
from app.worker import Worker

PID = uuid.UUID("00000000-0000-4000-8000-000000000002")


class FakeBackend:
    def __init__(self, r, text: str, *, modeling_outcome: str = "succeeded", busy_times: int = 0) -> None:
        self.r = r
        self.text = text
        self.outcome = modeling_outcome
        self.busy_times = busy_times
        self.completed: list[AgentCompleteRequest] = []
        self.submitted: list = []
        self.runs: list = []
        self.started: set = set()
        self.mjob = uuid.uuid4()

    def start(self, job_id, worker_id):
        if job_id in self.started:
            return JobStartResponse(proceed=False, reason="job is running")
        self.started.add(job_id)
        return JobStartResponse(proceed=True)

    def context(self, job_id):
        return AgentContext(
            job_id=job_id, project_id=PID, project_name="P", inventory=SceneInventory(), user_message=self.text
        )

    def submit_plan(self, job_id, body):
        if self.busy_times:
            self.busy_times -= 1
            raise BackendRejected(409, {"detail": "busy", "code": "project_busy"})
        self.submitted.append(body)
        # Simulate the Blender worker publishing progress + terminal events for the modeling job.
        for type_, msg in [
            ("blender.started", "Building in Blender"),
            ("blender.progress", "Exporting GLB"),
            ("version.created", "Version 1 saved"),
            ("job.completed" if self.outcome == "succeeded" else "job.failed", "done"),
        ]:
            ev = JobEvent(type=type_, job_id=self.mjob, message=msg)
            self.r.xadd(event_stream_key(self.mjob), {"event": ev.model_dump_json()})
        from forma3d_shared.schemas.agent import SubmitPlanResponse

        return SubmitPlanResponse(modeling_job_id=self.mjob, operation_batch_id=uuid.uuid4())

    def get_job(self, job_id):
        return {
            "status": self.outcome,
            "error_message": None if self.outcome == "succeeded" else "Bevel failed",
            "error_code": None if self.outcome == "succeeded" else "operation_failed",
        }

    def record_run(self, job_id, body):
        self.runs.append(body)

    def complete(self, job_id, body):
        self.completed.append(body)


def events(r, job_id) -> list[str]:
    return [json.loads(f["event"])["type"] for _id, f in r.xrange(event_stream_key(job_id))]


def runner(r, backend) -> AgentRunner:
    return AgentRunner(
        r, backend, Planner(FakeProvider()), worker_id="t", modeling_wait_seconds=5, sleep=lambda _s: None
    )


def spec() -> AgentJobSpec:
    return AgentJobSpec(job_id=uuid.uuid4(), project_id=PID, conversation_id=uuid.uuid4(), message_id=uuid.uuid4())


def test_modeling_flow_relays_blender_events_and_completes():
    r = fakeredis.FakeRedis(decode_responses=True)
    backend = FakeBackend(r, "Create a modern wooden desk")
    s = spec()
    runner(r, backend).run(s)
    assert backend.submitted and backend.submitted[0].summary == "Create a modern wooden desk"
    assert backend.completed[0].success and backend.completed[0].modeling_job_id == backend.mjob
    assert events(r, s.job_id) == [
        "agent.started",
        "agent.progress",
        "plan.created",
        "plan.validated",
        "blender.started",
        "blender.progress",
        "version.created",
    ]
    assert backend.runs and backend.runs[0].agent_name == "planner"


def test_question_does_not_submit():
    r = fakeredis.FakeRedis(decode_responses=True)
    backend = FakeBackend(r, "how many objects are there?")
    runner(r, backend).run(spec())
    assert not backend.submitted and backend.completed[0].intent == "question" and backend.completed[0].success


def test_blender_failure_is_reported_and_head_kept():
    r = fakeredis.FakeRedis(decode_responses=True)
    backend = FakeBackend(r, "Create a desk", modeling_outcome="failed")
    runner(r, backend).run(spec())
    done = backend.completed[0]
    assert not done.success and done.error_code == "operation_failed"
    assert "previous version is unchanged" in done.reply


def test_busy_project_is_retried():
    r = fakeredis.FakeRedis(decode_responses=True)
    backend = FakeBackend(r, "Create a desk", busy_times=2)
    s = spec()
    runner(r, backend).run(s)
    assert backend.completed[0].success
    assert "agent.progress" in events(r, s.job_id)


def test_cancel_before_planning():
    r = fakeredis.FakeRedis(decode_responses=True)
    backend = FakeBackend(r, "Create a desk")
    s = spec()
    r.set(cancel_flag_key(s.job_id), "1")
    runner(r, backend).run(s)
    assert not backend.submitted and backend.completed[0].error_code == "cancelled"


def test_worker_acks_after_completion_and_skips_duplicates(tmp_path: Path):
    r = fakeredis.FakeRedis(decode_responses=True)
    backend = FakeBackend(r, "how many objects?")
    cfg = AgentConfig(worker_id="w1", liveness_file=tmp_path / "alive", llm_provider="fake")
    worker = Worker(cfg, r=r, backend=backend, planner=Planner(FakeProvider()))
    worker.ensure_group()
    s = spec()
    r.xadd(AGENT_JOB_STREAM, {"job": s.model_dump_json()})
    r.xadd(AGENT_JOB_STREAM, {"job": s.model_dump_json()})  # duplicate delivery
    r.xadd(AGENT_JOB_STREAM, {"job": "not json"})
    assert worker.run_once(block_ms=10) + worker.run_once(block_ms=10) + worker.run_once(block_ms=10) == 3
    assert len(backend.completed) == 1
    assert r.xpending(AGENT_JOB_STREAM, "agent-workers")["pending"] == 0

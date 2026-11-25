"""In-memory stand-in for the backend + Blender worker, faithful to the internal API contract
(staged results, attempt idempotency, commit/discard, restore, complete) and to the DSL's geometry
(world locations, children following their parent, primitive dimensions). Lets the LangGraph
workflow run end to end in unit tests."""

from __future__ import annotations

import copy
import uuid
from typing import Any

from forma3d_shared.schemas.agent import (
    AgentCompleteRequest,
    AgentContext,
    AgentRunRecord,
    ChatTurn,
    RestoreRequest,
    RestoreResponse,
    StagedCommitResponse,
    SubmitPlanRequest,
    SubmitPlanResponse,
)
from forma3d_shared.schemas.events import JobEvent, event_stream_key
from forma3d_shared.schemas.job import JobStartResponse
from forma3d_shared.schemas.scene_object import SceneInventory

from app.backend_client import BackendRejected, BackendUnavailable

PID = uuid.UUID("00000000-0000-4000-8000-000000000002")


def _dims(t: str, p: dict[str, Any]) -> list[float]:
    if t == "create_cube":
        return p.get("dimensions", [1, 1, 1])
    if t in ("create_cylinder", "create_cone"):
        r = p["radius"] if t == "create_cylinder" else p["radius_bottom"]
        return [2 * r, 2 * r, p["depth"]]
    if t == "create_sphere":
        return [2 * p["radius"]] * 3
    if t == "create_torus":
        return [2 * (p["major_radius"] + p["minor_radius"])] * 2 + [2 * p["minor_radius"]]
    return [*p["size"], 0.0]


def simulate(inv: dict[str, Any], ops: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply DSL operations to an inventory dict the way the Blender runtime does."""
    inv = copy.deepcopy(inv)
    objs = {o["object_id"]: o for o in inv["objects"]}
    mats = {m["material_id"]: m for m in inv["materials"]}

    def descendants(oid: str) -> list[str]:
        out, stack = [], [k for k, o in objs.items() if o.get("parent_id") == oid]
        while stack:
            cur = stack.pop()
            out.append(cur)
            stack.extend(k for k, o in objs.items() if o.get("parent_id") == cur)
        return out

    for op in ops:
        t, p, oid = op["operation_type"], op.get("parameters", {}), op.get("target_object_id")
        if t.startswith("create_") and oid:
            dims = _dims(t, p)
            objs[oid] = {
                "object_id": oid,
                "name": p["name"],
                "type": "MESH",
                "component_type": (op.get("metadata") or {}).get("component"),
                "parent_id": p.get("parent_id"),
                "location": list(p.get("location", [0, 0, 0])),
                "rotation": list(p.get("rotation", [0, 0, 0])),
                "dimensions": [float(d) for d in dims],
                "material_ids": [p["material_id"]] if p.get("material_id") else [],
                "visible": True,
                "mesh": {"vertices": 8, "edges": 12, "faces": 6, "triangles": 12},
            }
        elif t == "create_material":
            mats[p["material_id"]] = {
                "material_id": p["material_id"],
                "name": p["name"],
                "base_color": p.get("base_color", "#CCCCCC"),
                "metallic": p.get("metallic", 0.0),
                "roughness": p.get("roughness", 0.5),
                "alpha": p.get("alpha", 1.0),
            }
        elif t == "update_material":
            m = mats[p["material_id"]]
            for k in ("name", "base_color", "metallic", "roughness", "alpha"):
                if p.get(k) is not None:
                    m[k] = p[k]
        elif t == "assign_material":
            objs[oid]["material_ids"] = [p["material_id"]]
        elif t == "set_dimensions":
            objs[oid]["dimensions"] = list(p["dimensions"])
        elif t == "move_object":
            old = objs[oid]["location"]
            new = list(p["location"]) if p.get("location") else [old[i] + p["offset"][i] for i in range(3)]
            delta = [new[i] - old[i] for i in range(3)]
            objs[oid]["location"] = new
            for d in descendants(oid):
                objs[d]["location"] = [objs[d]["location"][i] + delta[i] for i in range(3)]
        elif t == "rotate_object":
            objs[oid]["rotation"] = list(p["rotation"])
        elif t == "rename_object":
            objs[oid]["name"] = p["name"]
        elif t == "set_visibility":
            objs[oid]["visible"] = p["visible"]
        elif t == "parent_object":
            objs[oid]["parent_id"] = p["parent_id"]
        elif t == "unparent_object":
            objs[oid]["parent_id"] = None
        elif t == "delete_object":
            for d in [oid, *descendants(oid)]:
                objs.pop(d, None)
    inv["objects"] = list(objs.values())
    inv["materials"] = list(mats.values())
    return inv


class SimBackend:
    def __init__(self, redis, *, fail_blender: int = 0) -> None:
        self.redis = redis
        self.versions: list[dict[str, Any]] = []  # [{"id", "number", "inventory", "parent"}]
        self.head: int | None = None  # index into versions
        self.messages: list[tuple[str, str]] = []
        self.jobs: dict[uuid.UUID, dict[str, Any]] = {}  # agent jobs
        self.mjobs: dict[uuid.UUID, dict[str, Any]] = {}  # modeling jobs
        self.completed: dict[uuid.UUID, AgentCompleteRequest] = {}
        self.runs: list[tuple[uuid.UUID, AgentRunRecord]] = []
        self.submissions = 0
        self.fail_blender = fail_blender  # number of builds that fail before builds succeed
        self.fail_next_commit_with: Exception | None = None
        self.thread = "thread-1"

    # ------------------------------------------------------------------ test helpers
    def post(self, text: str, selected: str | list[str] | None = None) -> uuid.UUID:
        jid = uuid.uuid4()
        self.messages.append(("user", text))
        sel = [selected] if isinstance(selected, str) else list(selected or [])
        self.jobs[jid] = {"status": "queued", "text": text, "selected": sel}
        return jid

    @property
    def inventory(self) -> dict[str, Any]:
        return self.versions[self.head]["inventory"] if self.head is not None else {"objects": [], "materials": []}

    def objects(self) -> dict[str, dict[str, Any]]:
        return {o["object_id"]: o for o in self.inventory["objects"]}

    # ------------------------------------------------------------------ internal API
    def start(self, job_id, worker_id, *, reclaim=False):  # noqa: ANN001
        job = self.jobs[job_id]
        if reclaim and job["status"] == "running":
            return JobStartResponse(proceed=True, reason="resumed")
        if job["status"] != "queued":
            return JobStartResponse(proceed=False, reason=f"job is {job['status']}")
        job["status"] = "running"
        return JobStartResponse(proceed=True)

    def context(self, job_id) -> AgentContext:  # noqa: ANN001
        job = self.jobs[job_id]
        head = self.versions[self.head] if self.head is not None else None
        idx = max(i for i, m in enumerate(self.messages) if m == ("user", job["text"]))
        history = [ChatTurn(role=r, content=c) for r, c in self.messages[:idx]][-12:]  # type: ignore[arg-type]
        return AgentContext(
            job_id=job_id,
            project_id=PID,
            project_name="Desk",
            inventory=SceneInventory.model_validate(self.inventory),
            history=history,
            user_message=job["text"],
            selected_object_id=job["selected"][-1] if job["selected"] else None,
            selected_object_ids=job["selected"],
            version_number=head["number"] if head else None,
            thread_id=self.thread,
            head_version_id=head["id"] if head else None,
            version_numbers=sorted((v["number"] for v in self.versions), reverse=True),
        )

    def submit_plan(self, job_id, body: SubmitPlanRequest) -> SubmitPlanResponse:  # noqa: ANN001
        job = self.jobs[job_id]
        attempts = job.setdefault("attempts", {})
        if body.attempt in attempts:
            return SubmitPlanResponse(modeling_job_id=attempts[body.attempt], operation_batch_id=uuid.uuid4())
        if any(m["status"] == "staged" for m in self.mjobs.values()):
            raise BackendRejected(409, {"detail": "busy", "code": "project_busy"})
        self.submissions += 1
        mid = uuid.uuid4()
        attempts[body.attempt] = mid
        if self.fail_blender > 0:
            self.fail_blender -= 1
            self.mjobs[mid] = {
                "status": "failed",
                "error_code": "operation_failed",
                "error_message": "Bevel failed on drawer_left",
            }
            event = "job.failed"
        else:
            built = simulate(self.inventory, body.operations)
            self.mjobs[mid] = {
                "status": "staged" if body.defer_commit else "succeeded",
                "inventory": built,
                "summary": body.summary,
                "error_code": None,
                "error_message": None,
            }
            event = "job.staged"
        for t in ("blender.started", "blender.progress", event):
            ev = JobEvent(type=t, job_id=mid, message=t)  # type: ignore[arg-type]
            self.redis.xadd(event_stream_key(mid), {"event": ev.model_dump_json()})
        return SubmitPlanResponse(modeling_job_id=mid, operation_batch_id=uuid.uuid4())

    def get_job(self, job_id) -> dict[str, Any]:  # noqa: ANN001
        j = self.mjobs[job_id]
        return {"status": j["status"], "error_code": j.get("error_code"), "error_message": j.get("error_message")}

    def staged_inventory(self, mid) -> SceneInventory:  # noqa: ANN001
        assert self.mjobs[mid]["status"] == "staged"
        return SceneInventory.model_validate(self.mjobs[mid]["inventory"])

    def commit_staged(self, mid) -> StagedCommitResponse:  # noqa: ANN001
        if self.fail_next_commit_with is not None:
            exc, self.fail_next_commit_with = self.fail_next_commit_with, None
            raise exc
        j = self.mjobs[mid]
        if j["status"] == "succeeded":
            v = j["version"]
            return StagedCommitResponse(version_id=v["id"], version_number=v["number"])
        assert j["status"] == "staged", j["status"]
        v = {
            "id": uuid.uuid4(),
            "number": len(self.versions) + 1,
            "inventory": j["inventory"],
            "parent": self.head,
            "summary": j["summary"],
        }
        self.versions.append(v)
        self.head = len(self.versions) - 1
        j.update(status="succeeded", version=v)
        return StagedCommitResponse(version_id=v["id"], version_number=v["number"])

    def discard_staged(self, mid, reason: str) -> None:  # noqa: ANN001
        if self.mjobs[mid]["status"] == "staged":
            self.mjobs[mid]["status"] = "discarded"

    def restore(self, job_id, body: RestoreRequest) -> RestoreResponse:  # noqa: ANN001
        if body.undo:
            parent = self.versions[self.head]["parent"] if self.head is not None else None
            if parent is None:
                raise BackendRejected(409, {"detail": "There is no earlier version", "code": "nothing_to_undo"})
            self.head = parent
        elif body.redo:
            children = [i for i, v in enumerate(self.versions) if v["parent"] == self.head]
            if not children:
                raise BackendRejected(409, {"detail": "There is nothing to redo", "code": "nothing_to_redo"})
            self.head = children[-1]
        else:
            self.head = next(i for i, v in enumerate(self.versions) if v["number"] == body.version_number)
        v = self.versions[self.head]
        return RestoreResponse(version_id=v["id"], version_number=v["number"])

    def record_run(self, job_id, body: AgentRunRecord) -> None:  # noqa: ANN001
        self.runs.append((job_id, body))

    def complete(self, job_id, body: AgentCompleteRequest) -> None:  # noqa: ANN001
        if job_id in self.completed:
            return
        self.completed[job_id] = body
        self.jobs[job_id]["status"] = "succeeded" if body.success else "failed"
        self.messages.append(("assistant", body.reply))


class FlakyBackend(SimBackend):
    """Backend that becomes unreachable once, at a chosen call (simulates a worker crash point)."""

    def __init__(self, redis, crash_on: str) -> None:
        super().__init__(redis)
        self.crash_on = crash_on

    def __getattribute__(self, name: str):  # noqa: ANN204
        crash_on = object.__getattribute__(self, "crash_on")
        if name == crash_on:
            object.__setattr__(self, "crash_on", "")
            raise BackendUnavailable(f"simulated crash before {name}")
        return object.__getattribute__(self, name)

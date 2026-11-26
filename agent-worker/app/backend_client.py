"""Internal API client (agent worker -> backend). The worker never touches the database."""

from __future__ import annotations

import time
import uuid
from typing import Any

import httpx
from forma3d_shared.schemas.agent import (
    AgentCompleteRequest,
    AgentContext,
    AgentRunRecord,
    RestoreRequest,
    RestoreResponse,
    StagedCommitResponse,
    SubmitPlanRequest,
    SubmitPlanResponse,
)
from forma3d_shared.schemas.job import JobStartResponse
from forma3d_shared.schemas.scene_object import SceneInventory


class BackendUnavailable(Exception):
    pass


class BackendRejected(Exception):
    """4xx from the backend: carries the error code and validation issues if any."""

    def __init__(self, status: int, body: dict[str, Any]) -> None:
        super().__init__(body.get("detail", f"backend returned {status}"))
        self.status = status
        self.code = body.get("code", "error")
        self.issues = body.get("issues", [])


class BackendClient:
    def __init__(self, base_url: str, token: str, *, retries: int = 5, client: httpx.Client | None = None) -> None:
        self.retries = retries
        self.http = client or httpx.Client(base_url=base_url, timeout=30.0)
        self.headers = {"X-Internal-Token": token}

    def _call(self, method: str, path: str, payload: dict | None = None) -> Any:
        delay = 1.0
        last: Exception | None = None
        for _ in range(self.retries):
            try:
                res = self.http.request(method, path, json=payload, headers=self.headers)
                if res.status_code >= 500:
                    raise BackendUnavailable(f"backend returned {res.status_code}")
                if res.status_code >= 400:
                    try:
                        body = res.json()
                    except ValueError:
                        body = {"detail": res.text[:300]}
                    raise BackendRejected(res.status_code, body)
                return res.json() if res.content else None
            except (httpx.TransportError, BackendUnavailable) as exc:
                last = exc
                time.sleep(delay)
                delay = min(delay * 2, 15)
        raise BackendUnavailable(str(last))

    def start(self, job_id: uuid.UUID, worker_id: str, *, reclaim: bool = False) -> JobStartResponse:
        return JobStartResponse.model_validate(
            self._call("POST", f"/internal/v1/jobs/{job_id}/start", {"worker_id": worker_id, "reclaim": reclaim})
        )

    def get_job(self, job_id: uuid.UUID) -> dict[str, Any]:
        return self._call("GET", f"/internal/v1/jobs/{job_id}")

    def context(self, job_id: uuid.UUID) -> AgentContext:
        return AgentContext.model_validate(self._call("GET", f"/internal/v1/agent-jobs/{job_id}/context"))

    def runtime_config(self) -> dict[str, Any]:
        """Overrides saved in the app's Configuration page: {"version": n, "values": {...}}."""
        return self._call("GET", "/internal/v1/config")

    def reference_bytes(self, job_id: uuid.UUID, reference_id: uuid.UUID) -> bytes:
        """A photo attached to the job's message (raw bytes; not JSON)."""
        delay = 1.0
        last: Exception | None = None
        for _ in range(self.retries):
            try:
                res = self.http.get(f"/internal/v1/agent-jobs/{job_id}/references/{reference_id}", headers=self.headers)
                if res.status_code >= 500:
                    raise BackendUnavailable(f"backend returned {res.status_code}")
                if res.status_code >= 400:
                    raise BackendRejected(res.status_code, {"detail": "The attached image is not available"})
                return res.content
            except (httpx.TransportError, BackendUnavailable) as exc:
                last = exc
                time.sleep(delay)
                delay = min(delay * 2, 15)
        raise BackendUnavailable(str(last))

    def save_reference_description(self, job_id: uuid.UUID, description: str) -> None:
        self._call(
            "POST", f"/internal/v1/agent-jobs/{job_id}/reference-description", {"description": description[:6000]}
        )

    def submit_plan(self, job_id: uuid.UUID, body: SubmitPlanRequest) -> SubmitPlanResponse:
        return SubmitPlanResponse.model_validate(
            self._call("POST", f"/internal/v1/agent-jobs/{job_id}/plan", body.model_dump(mode="json"))
        )

    def record_run(self, job_id: uuid.UUID, body: AgentRunRecord) -> None:
        self._call("POST", f"/internal/v1/agent-jobs/{job_id}/runs", body.model_dump(mode="json"))

    def complete(self, job_id: uuid.UUID, body: AgentCompleteRequest) -> None:
        self._call("POST", f"/internal/v1/agent-jobs/{job_id}/complete", body.model_dump(mode="json"))

    # ---------------------------------------------------------------- Phase 5: staged results, versions
    def staged_inventory(self, modeling_job_id: uuid.UUID) -> SceneInventory:
        return SceneInventory.model_validate(self._call("GET", f"/internal/v1/jobs/{modeling_job_id}/staged"))

    def commit_staged(self, modeling_job_id: uuid.UUID) -> StagedCommitResponse:
        return StagedCommitResponse.model_validate(self._call("POST", f"/internal/v1/jobs/{modeling_job_id}/commit"))

    def discard_staged(self, modeling_job_id: uuid.UUID, reason: str) -> None:
        self._call("POST", f"/internal/v1/jobs/{modeling_job_id}/discard", {"reason": reason[:500]})

    def restore(self, job_id: uuid.UUID, body: RestoreRequest) -> RestoreResponse:
        return RestoreResponse.model_validate(
            self._call("POST", f"/internal/v1/agent-jobs/{job_id}/restore", body.model_dump(mode="json"))
        )

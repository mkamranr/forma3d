"""Internal API client (worker -> backend), authenticated with the service token."""

from __future__ import annotations

import time
import uuid

import httpx
from forma3d_shared.schemas.job import BlenderJobResult, JobStartResponse


class BackendUnavailable(Exception):
    pass


class BackendClient:
    def __init__(self, base_url: str, token: str, *, retries: int = 5, client: httpx.Client | None = None) -> None:
        self.retries = retries
        self.http = client or httpx.Client(base_url=base_url, timeout=30.0)
        self.headers = {"X-Internal-Token": token}

    def _post(self, path: str, payload: dict) -> dict:
        delay = 1.0
        last: Exception | None = None
        for _ in range(self.retries):
            try:
                res = self.http.post(path, json=payload, headers=self.headers)
                if res.status_code >= 500:
                    raise BackendUnavailable(f"backend returned {res.status_code}")
                res.raise_for_status()
                return res.json()
            except (httpx.TransportError, BackendUnavailable) as exc:
                last = exc
                time.sleep(delay)
                delay = min(delay * 2, 15)
        raise BackendUnavailable(str(last))

    def start(self, job_id: uuid.UUID, worker_id: str, *, reclaim: bool = False) -> JobStartResponse:
        return JobStartResponse.model_validate(
            self._post(f"/internal/v1/jobs/{job_id}/start", {"worker_id": worker_id, "reclaim": reclaim})
        )

    def complete(self, result: BlenderJobResult) -> dict:
        return self._post(f"/internal/v1/jobs/{result.job_id}/complete", result.model_dump(mode="json"))

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from app.services.storage import ProjectStorage, StoragePathError


async def test_health(client: AsyncClient) -> None:
    res = await client.get("/health")
    assert res.status_code == 200 and res.json() == {"status": "ok"}


async def test_ready_checks_db_and_redis(client: AsyncClient) -> None:
    res = await client.get("/health/ready")
    assert res.status_code == 200
    assert res.json()["checks"] == {"database": "ok", "redis": "ok"}


async def test_ready_reports_unavailable_redis(client: AsyncClient) -> None:
    client.app.state.redis = None  # type: ignore[attr-defined]
    res = await client.get("/health/ready")
    assert res.status_code == 503
    assert res.json()["checks"]["redis"] == "unavailable"


async def test_correlation_id_echoed_and_generated(client: AsyncClient) -> None:
    res = await client.get("/health", headers={"X-Request-ID": "abc-123"})
    assert res.headers["x-request-id"] == "abc-123"
    res = await client.get("/health", headers={"X-Request-ID": "bad id\nwith newline"})
    assert res.headers["x-request-id"] != "bad id\nwith newline"
    assert len(res.headers["x-request-id"]) == 32


def test_storage_paths_are_server_generated(tmp_path) -> None:
    storage = ProjectStorage(tmp_path)
    u, p, j = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    assert storage.version_dir(u, p, 3) == f"projects/{u}/{p}/versions/v0003"
    assert storage.staging_dir(u, p, j).endswith(f"staging/{j}")
    assert storage.resolve(storage.version_dir(u, p, 1)).is_relative_to(tmp_path.resolve())


@pytest.mark.parametrize("bad", ["../etc/passwd", "projects/../../x", "/etc/passwd", "", "a\x00b"])
def test_storage_rejects_traversal(tmp_path, bad: str) -> None:
    with pytest.raises(StoragePathError):
        ProjectStorage(tmp_path).resolve(bad)


def test_storage_rejects_non_uuid_ids(tmp_path) -> None:
    with pytest.raises(ValueError):
        ProjectStorage(tmp_path).project_dir("../x", uuid.uuid4())  # type: ignore[arg-type]

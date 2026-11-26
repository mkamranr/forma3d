"""In-app Configuration: admin access, settings overrides (validation, effect, secrets, other replicas),
sign-up control, user management and the LLM connection test relay."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from httpx import AsyncClient

from app.core.config import Settings
from app.core.runtime_config import RuntimeConfig
from app.models import User
from app.services.auth_service import ensure_admins
from forma3d_shared.examples import desk_batch
from forma3d_shared.runtime_config import CONFIG_VERSION_KEY, LLM_TEST_QUEUE, LLM_TEST_RESULT
from tests.conftest import auth, register
from tests.test_modeling_pipeline import INTERNAL, pipeline_settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return pipeline_settings(tmp_path)


def field(cfg: dict, key: str) -> dict:
    return next(f for g in cfg["groups"] for f in g["fields"] if f["key"] == key)


async def _admin_and_user(client: AsyncClient) -> tuple[dict, dict]:
    a = await register(client, email="admin@example.com", name="Ada")
    b = await register(client, email="bob@example.com", name="Bob")
    assert a["user"]["is_admin"] is True and b["user"]["is_admin"] is False  # the first account administers
    return auth(a["access_token"]), auth(b["access_token"])


async def test_only_admins_see_the_configuration(client: AsyncClient) -> None:
    ha, hb = await _admin_and_user(client)
    assert (await client.get("/api/v1/auth/me", headers=ha)).json()["is_admin"] is True
    for method, url in (
        ("GET", "/api/v1/admin/config"),
        ("GET", "/api/v1/admin/users"),
        ("PUT", "/api/v1/admin/config"),
    ):
        res = await client.request(method, url, headers=hb, json={"values": {}})
        assert res.status_code == 403 and res.json()["code"] == "admin_only", url
    assert (await client.get("/api/v1/admin/config")).status_code == 401
    cfg = (await client.get("/api/v1/admin/config", headers=ha)).json()
    assert [g["id"] for g in cfg["groups"]] == ["ai", "rendering", "limits", "accounts"]
    llm = field(cfg, "llm_provider")
    assert llm["type"] == "enum" and {o["value"] for o in llm["options"]} >= {"vllm", "fake"}
    assert field(cfg, "rate_limit_jobs_per_minute")["source"] in ("env", "default")


async def test_overrides_are_validated_applied_and_reset(client: AsyncClient) -> None:
    ha, _ = await _admin_and_user(client)
    bad = [
        {"modeling_max_scene_objects": 0},
        {"modeling_max_scene_objects": "many"},
        {"llm_thinking": "maybe"},
        {"llm_base_url": "ftp://x"},
        {"render_eevee_enabled": "yes"},
        {"nope": 1},
    ]
    for values in bad:
        res = await client.put("/api/v1/admin/config", json={"values": values}, headers=ha)
        assert res.status_code == 422 and res.json()["code"] == "invalid_setting", values

    res = await client.put(
        "/api/v1/admin/config",
        json={
            "values": {"modeling_max_scene_objects": 2, "llm_base_url": "http://gpu:8000/v1/", "llm_thinking": "off"}
        },
        headers=ha,
    )
    assert res.status_code == 200, res.text
    f = field(res.json(), "modeling_max_scene_objects")
    assert f["value"] == 2 and f["source"] == "app" and f["default"] == 500
    assert field(res.json(), "llm_base_url")["value"] == "http://gpu:8000/v1"  # normalised

    # The limit is enforced by the validator right away.
    pid = (await client.post("/api/v1/projects", json={"name": "Desk"}, headers=ha)).json()["id"]
    res = await client.post(f"/api/v1/projects/{pid}/modeling/execute", json=desk_batch(), headers=ha)
    assert res.status_code == 422 and "invalid_batch" in res.text

    res = await client.put("/api/v1/admin/config", json={"values": {"modeling_max_scene_objects": None}}, headers=ha)
    assert field(res.json(), "modeling_max_scene_objects")["source"] in ("env", "default")
    res = await client.post(f"/api/v1/projects/{pid}/modeling/execute", json=desk_batch(), headers=ha)
    assert res.status_code == 202


async def test_secrets_are_write_only_and_reach_workers(client: AsyncClient) -> None:
    ha, _ = await _admin_and_user(client)
    res = await client.put("/api/v1/admin/config", json={"values": {"llm_api_key": "sk-test-123"}}, headers=ha)
    key = field(res.json(), "llm_api_key")
    assert key["value"] is None and key["is_set"] is True
    assert "sk-test-123" not in res.text
    assert "sk-test-123" not in (await client.get("/api/v1/config")).text
    internal = (await client.get("/internal/v1/config", headers=INTERNAL)).json()
    assert internal["values"]["llm_api_key"] == "sk-test-123" and internal["version"] >= 1
    assert (await client.get("/internal/v1/config")).status_code in (401, 403)


async def test_other_backend_replicas_pick_up_changes(client: AsyncClient, settings: Settings) -> None:
    ha, _ = await _admin_and_user(client)
    app = client.app  # type: ignore[attr-defined]
    replica = RuntimeConfig(settings)
    await replica.refresh(app.state.db.sessionmaker, app.state.redis, force=True)
    assert replica.settings.output_retention_days == settings.output_retention_days
    await client.put("/api/v1/admin/config", json={"values": {"output_retention_days": 7}}, headers=ha)
    assert int(await app.state.redis.get(CONFIG_VERSION_KEY)) >= 1
    await replica.refresh(app.state.db.sessionmaker, app.state.redis, force=True)
    assert replica.settings.output_retention_days == 7


async def test_sign_ups_can_be_closed(client: AsyncClient) -> None:
    ha, _ = await _admin_and_user(client)
    assert (await client.get("/api/v1/config")).json()["registration_open"] is True
    await client.put("/api/v1/admin/config", json={"values": {"registration_open": False}}, headers=ha)
    public = (await client.get("/api/v1/config")).json()
    assert public["registration_open"] is False and public["default_export_format"] == "glb"
    res = await client.post(
        "/api/v1/auth/register", json={"email": "c@example.com", "password": "s3cret-pass", "display_name": "C"}
    )
    assert res.status_code == 403 and res.json()["code"] == "registration_closed"


async def test_user_management(client: AsyncClient) -> None:
    ha, hb = await _admin_and_user(client)
    await client.post("/api/v1/projects", json={"name": "Lamp"}, headers=hb)
    users = (await client.get("/api/v1/admin/users", headers=ha)).json()
    assert users["total"] == 2
    bob = next(u for u in users["items"] if u["email"] == "bob@example.com")
    ada = next(u for u in users["items"] if u["email"] == "admin@example.com")
    assert bob["projects"] == 1 and bob["is_admin"] is False
    assert [u["email"] for u in (await client.get("/api/v1/admin/users?q=BOB", headers=ha)).json()["items"]] == [
        "bob@example.com"
    ]

    # Guard rails
    res = await client.patch(f"/api/v1/admin/users/{ada['id']}", json={"is_admin": False}, headers=ha)
    assert res.status_code == 409 and res.json()["code"] == "self_change"
    res = await client.patch(f"/api/v1/admin/users/{ada['id']}", json={"is_active": False}, headers=ha)
    assert res.status_code == 409

    # Deactivating signs Bob out immediately (access token rejected, refresh revoked).
    res = await client.patch(f"/api/v1/admin/users/{bob['id']}", json={"is_active": False}, headers=ha)
    assert res.status_code == 200 and res.json()["is_active"] is False
    assert (await client.get("/api/v1/projects", headers=hb)).status_code == 401
    login = await client.post("/api/v1/auth/login", json={"email": "bob@example.com", "password": "s3cret-pass"})
    assert login.status_code == 401

    # Reactivate + make admin; now Ada may step down (Bob is another active admin).
    await client.patch(f"/api/v1/admin/users/{bob['id']}", json={"is_active": True, "is_admin": True}, headers=ha)
    hb = auth(
        (await client.post("/api/v1/auth/login", json={"email": "bob@example.com", "password": "s3cret-pass"})).json()[
            "access_token"
        ]
    )
    res = await client.patch(f"/api/v1/admin/users/{ada['id']}", json={"is_admin": False}, headers=hb)
    assert res.status_code == 200 and res.json()["is_admin"] is False
    res = await client.patch(f"/api/v1/admin/users/{bob['id']}", json={"is_admin": False}, headers=ha)
    assert res.status_code == 403  # Ada is no longer an admin


async def test_ensure_admins_promotes_configured_or_oldest(client: AsyncClient, settings: Settings) -> None:
    await _admin_and_user(client)
    sessionmaker = client.app.state.db.sessionmaker  # type: ignore[attr-defined]
    async with sessionmaker() as s:
        await s.execute(User.__table__.update().values(is_admin=False))
        await s.commit()
    async with sessionmaker() as s:
        await ensure_admins(s, settings.model_copy(update={"admin_emails": "BOB@example.com"}))
    async with sessionmaker() as s:
        admins = {r.email for r in (await s.execute(User.__table__.select().where(User.is_admin.is_(True)))).all()}
    assert admins == {"bob@example.com"}
    async with sessionmaker() as s:
        await s.execute(User.__table__.update().values(is_admin=False))
        await s.commit()
        await ensure_admins(s, settings)
    async with sessionmaker() as s:
        admins = {r.email for r in (await s.execute(User.__table__.select().where(User.is_admin.is_(True)))).all()}
    assert admins == {"admin@example.com"}  # nobody configured: the oldest account


async def test_llm_test_is_relayed_to_an_agent_worker(client: AsyncClient) -> None:
    ha, _ = await _admin_and_user(client)
    redis = client.app.state.redis  # type: ignore[attr-defined]
    res = await client.post("/api/v1/admin/config/test-llm", json={"values": {}}, headers=ha)
    assert res.status_code == 503 and res.json()["code"] == "no_agent_worker"

    await redis.set("workers:agent:w1", 1)
    await client.put("/api/v1/admin/config", json={"values": {"llm_model": "saved-model"}}, headers=ha)

    async def worker() -> None:
        while not (raw := await redis.lpop(LLM_TEST_QUEUE)):  # noqa: ASYNC110
            await asyncio.sleep(0.05)
        req = json.loads(raw)
        assert req["values"] == {"llm_model": "saved-model", "llm_base_url": "http://candidate:8000/v1"}
        await redis.set(LLM_TEST_RESULT.format(id=req["id"]), json.dumps({"ok": True, "checks": []}))

    task = asyncio.create_task(worker())
    res = await client.post(
        "/api/v1/admin/config/test-llm", json={"values": {"llm_base_url": "http://candidate:8000/v1"}}, headers=ha
    )
    await task
    assert res.status_code == 200 and res.json() == {"ok": True, "checks": []}

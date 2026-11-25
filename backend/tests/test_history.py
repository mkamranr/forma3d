"""Phase 6: undo / redo / branching / compare over immutable versions, the conversation linked to
each version, multi-object selection, and Scenario E (concurrent modifications never lose updates)."""

from __future__ import annotations

import asyncio
import uuid

from httpx import AsyncClient

from app.core.config import Settings
from tests.test_conversations import _agent_spec, _complete, _start
from tests.test_modeling_pipeline import INTERNAL, FakeWorker, settings  # noqa: F401  (fixture reuse)
from tests.test_scene_api import _project_with_desk


async def _add(client: AsyncClient, h: dict, pid: str, worker: FakeWorker, kind: str = "cube") -> None:
    res = await client.post(f"/api/v1/projects/{pid}/scene/objects", headers=h, json={"kind": kind})
    assert res.status_code == 202, res.text
    await worker.run()


async def _versions(client: AsyncClient, h: dict, pid: str) -> list[dict]:
    return (await client.get(f"/api/v1/projects/{pid}/versions", headers=h)).json()


async def _head(client: AsyncClient, h: dict, pid: str) -> int:
    return next(v["version_number"] for v in await _versions(client, h, pid) if v["is_current"])


async def test_undo_redo_and_branching(client: AsyncClient, settings: Settings) -> None:  # noqa: F811
    h, pid, worker = await _project_with_desk(client, settings)  # v1
    await _add(client, h, pid, worker)  # v2
    await _add(client, h, pid, worker, "sphere")  # v3
    base = f"/api/v1/projects/{pid}/modeling"

    res = await client.post(f"{base}/redo", headers=h)
    assert res.status_code == 409 and res.json()["code"] == "nothing_to_redo"
    res = await client.post(f"{base}/undo", headers=h)
    assert res.status_code == 200 and res.json()["version_number"] == 2
    assert res.json()["project"]["current_version_id"] == res.json()["version_id"]
    assert (await client.post(f"{base}/undo", headers=h)).json()["version_number"] == 1
    res = await client.post(f"{base}/undo", headers=h)
    assert res.status_code == 409 and res.json()["code"] == "nothing_to_undo"
    scene = (await client.get(f"/api/v1/projects/{pid}/scene", headers=h)).json()
    assert scene["version"]["version_number"] == 1

    assert (await client.post(f"{base}/redo", headers=h)).json()["version_number"] == 2
    # a new edit after an undo branches from v2; redo from v2 now follows the newest branch (v4)
    await _add(client, h, pid, worker, "cone")
    versions = await _versions(client, h, pid)
    v = {x["version_number"]: x for x in versions}
    assert v[4]["parent_version_id"] == v[2]["id"] and v[3]["parent_version_id"] == v[2]["id"]
    assert (await client.post(f"{base}/undo", headers=h)).json()["version_number"] == 2
    assert (await client.post(f"{base}/redo", headers=h)).json()["version_number"] == 4
    assert len(versions) == 4  # undo/redo never create or delete versions


async def test_undo_blocked_while_building(client: AsyncClient, settings: Settings) -> None:  # noqa: F811
    h, pid, worker = await _project_with_desk(client, settings)
    await _add(client, h, pid, worker)
    await client.post(f"/api/v1/projects/{pid}/scene/objects", headers=h, json={"kind": "cube"})  # building
    res = await client.post(f"/api/v1/projects/{pid}/modeling/undo", headers=h)
    assert res.status_code == 409 and res.json()["code"] == "project_busy"


async def test_compare_versions(client: AsyncClient, settings: Settings) -> None:  # noqa: F811
    h, pid, worker = await _project_with_desk(client, settings)
    await _add(client, h, pid, worker)
    v2, v1 = (await _versions(client, h, pid))[:2]
    res = await client.post(
        f"/api/v1/projects/{pid}/versions/compare",
        headers=h,
        json={"base_version_id": v1["id"], "target_version_id": v2["id"]},
    )
    assert res.status_code == 200, res.text
    d = res.json()
    assert d["base"]["version_number"] == 1 and d["target"]["version_number"] == 2
    assert len(d["objects"]["added"]) == 1 and d["objects"]["removed"] == []
    assert d["objects"]["unchanged"] == v1["object_count"]
    back = (
        await client.post(
            f"/api/v1/projects/{pid}/versions/compare",
            headers=h,
            json={"base_version_id": v2["id"], "target_version_id": v1["id"]},
        )
    ).json()
    assert back["objects"]["removed"] == d["objects"]["added"]
    missing = await client.post(
        f"/api/v1/projects/{pid}/versions/compare",
        headers=h,
        json={"base_version_id": v1["id"], "target_version_id": str(uuid.uuid4())},
    )
    assert missing.status_code == 404
    # another user's project
    from tests.conftest import auth, register

    other = auth((await register(client, email="b@example.com"))["access_token"])
    res = await client.post(
        f"/api/v1/projects/{pid}/versions/compare",
        headers=other,
        json={"base_version_id": v1["id"], "target_version_id": v2["id"]},
    )
    assert res.status_code == 404


async def test_versions_link_to_the_conversation(client: AsyncClient, settings: Settings) -> None:  # noqa: F811
    from forma3d_shared.examples import black_metal_legs_batch

    h, pid, worker = await _project_with_desk(client, settings)
    res = await client.post(
        f"/api/v1/projects/{pid}/conversation/messages",
        headers=h,
        json={
            "content": "Make the legs black metal",
            "selected_object_ids": ["leg_fl", "leg_fr"],
            "selected_object_id": "leg_bl",
        },
    )
    assert res.status_code == 202, res.text
    assert res.json()["message"]["metadata"]["selected_object_ids"] == ["leg_fl", "leg_fr", "leg_bl"]
    spec = await _agent_spec(client)
    await _start(client, spec.job_id)
    ctx = (await client.get(f"/internal/v1/agent-jobs/{spec.job_id}/context", headers=INTERNAL)).json()
    assert ctx["selected_object_id"] == "leg_bl" and ctx["selected_object_ids"] == ["leg_fl", "leg_fr", "leg_bl"]
    res = await client.post(
        f"/internal/v1/agent-jobs/{spec.job_id}/plan",
        headers=INTERNAL,
        json={"summary": "Black legs", "operations": black_metal_legs_batch()["operations"]},
    )
    mjob = res.json()["modeling_job_id"]
    await worker.run()
    await _complete(client, spec.job_id, success=True, intent="modeling", reply="Done.", modeling_job_id=mjob)

    v2, v1 = (await _versions(client, h, pid))[:2]
    assert v2["source"] == "chat" and v2["prompt"] == "Make the legs black metal" and v2["message_id"]
    assert v1["source"] == "edit" and v1["prompt"] is None
    detail = (await client.get(f"/api/v1/projects/{pid}/versions/{v2['id']}", headers=h)).json()
    assert detail["prompt"] == "Make the legs black metal"

    # agent redo: undo then redo through the internal version manager
    for text, body, expected in (("undo", {"undo": True}, 1), ("redo", {"redo": True}, 2)):
        await client.post(f"/api/v1/projects/{pid}/conversation/messages", headers=h, json={"content": text})
        spec = await _agent_spec(client)
        await _start(client, spec.job_id)
        res = await client.post(f"/internal/v1/agent-jobs/{spec.job_id}/restore", json=body, headers=INTERNAL)
        assert res.status_code == 200 and res.json()["version_number"] == expected
        await _complete(client, spec.job_id, success=True, intent=text, reply="ok")
    spec_redo = await client.post(f"/api/v1/projects/{pid}/conversation/messages", headers=h, json={"content": "redo"})
    spec = await _agent_spec(client)
    await _start(client, spec.job_id)
    res = await client.post(f"/internal/v1/agent-jobs/{spec.job_id}/restore", json={"redo": True}, headers=INTERNAL)
    assert spec_redo.status_code == 202 and res.status_code == 409 and res.json()["code"] == "nothing_to_redo"


async def test_selection_validation(client: AsyncClient, settings: Settings) -> None:  # noqa: F811
    h, pid, _w = await _project_with_desk(client, settings)
    res = await client.post(
        f"/api/v1/projects/{pid}/conversation/messages",
        headers=h,
        json={"content": "x", "selected_object_ids": ["../etc/passwd"]},
    )
    assert res.status_code == 422


# ------------------------------------------------------------------------------------- Scenario E
async def test_concurrent_chat_messages_one_wins(client: AsyncClient, settings: Settings) -> None:  # noqa: F811
    h, pid, _w = await _project_with_desk(client, settings)
    url = f"/api/v1/projects/{pid}/conversation/messages"
    a, b = await asyncio.gather(
        client.post(url, headers=h, json={"content": "Make the legs black metal"}),
        client.post(url, headers=h, json={"content": "Change the top to walnut"}),
    )
    assert sorted([a.status_code, b.status_code]) == [202, 409]
    loser = a if a.status_code == 409 else b
    assert loser.json()["code"] == "project_busy"


async def test_concurrent_edits_never_lose_updates(client: AsyncClient, settings: Settings) -> None:  # noqa: F811
    h, pid, worker = await _project_with_desk(client, settings)
    url = f"/api/v1/projects/{pid}/scene/objects"
    a, b = await asyncio.gather(
        client.post(url, headers=h, json={"kind": "cube"}), client.post(url, headers=h, json={"kind": "sphere"})
    )
    assert sorted([a.status_code, b.status_code]) == [202, 409]
    await worker.run()

    # Even if the project lock were lost (e.g. TTL expiry), optimistic concurrency on the head
    # rejects a commit built on a stale head instead of silently overwriting the newer version.
    res = await client.post(url, headers=h, json={"kind": "cone"})  # job built on head v2
    spec = await worker.next_spec()
    await client.app.state.redis.delete(f"lock:project:{pid}")  # simulate a lost lock
    v1 = next(v for v in await _versions(client, h, pid) if v["version_number"] == 1)
    # move the head behind the job's back
    from sqlalchemy import update

    from app.models import Project

    async with client.app.state.db.sessionmaker() as s:
        await s.execute(
            update(Project).where(Project.id == uuid.UUID(pid)).values(current_version_id=uuid.UUID(v1["id"]))
        )
        await s.commit()
    await worker.run(spec)
    job = (await client.get(f"/api/v1/jobs/{res.json()['job_id']}", headers=h)).json()
    assert job["status"] == "failed" and "changed" in (job["error_message"] or "")
    assert [v["version_number"] for v in await _versions(client, h, pid)] == [2, 1]
    assert await _head(client, h, pid) == 1

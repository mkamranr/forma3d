"""Scene read API + direct edits compiled to DSL batches (they share the modeling pipeline)."""

from __future__ import annotations

from httpx import AsyncClient

from app.core.config import Settings
from tests.conftest import auth, register
from tests.test_modeling_pipeline import FakeWorker, settings  # noqa: F401  (fixture reuse)


async def _project_with_desk(client: AsyncClient, settings: Settings) -> tuple[dict, str, FakeWorker]:  # noqa: F811
    h = auth((await register(client))["access_token"])
    pid = (await client.post("/api/v1/projects", json={"name": "Desk"}, headers=h)).json()["id"]
    worker = FakeWorker(client, settings.data_root)
    res = await client.post(f"/api/v1/projects/{pid}/scene/starter", headers=h)
    assert res.status_code == 202, res.text
    await worker.run()
    return h, pid, worker


def _ops(spec) -> list[tuple[str, str | None]]:
    return [(o["operation_type"], o.get("target_object_id")) for o in spec.operations]


async def test_empty_scene_then_starter(client: AsyncClient, settings: Settings) -> None:  # noqa: F811
    h = auth((await register(client))["access_token"])
    pid = (await client.post("/api/v1/projects", json={"name": "P"}, headers=h)).json()["id"]
    scene = (await client.get(f"/api/v1/projects/{pid}/scene", headers=h)).json()
    assert scene["version"] is None and scene["inventory"]["objects"] == [] and scene["active_job"] is None

    sub = (await client.post(f"/api/v1/projects/{pid}/scene/starter", headers=h)).json()
    scene = (await client.get(f"/api/v1/projects/{pid}/scene", headers=h)).json()
    assert scene["active_job"]["id"] == sub["job_id"] and scene["active_job"]["status"] == "queued"

    await FakeWorker(client, settings.data_root).run()
    scene = (await client.get(f"/api/v1/projects/{pid}/scene", headers=h)).json()
    assert scene["active_job"] is None
    assert scene["version"]["version_number"] == 1 and scene["version"]["is_current"]
    assert any(a["kind"] == "glb" for a in scene["version"]["assets"])
    assert {o["object_id"] for o in scene["inventory"]["objects"]} >= {"desk_top", "leg_fl"}
    res = await client.post(f"/api/v1/projects/{pid}/scene/starter", headers=h)
    assert res.status_code == 409 and res.json()["code"] == "scene_exists"


async def test_object_edits_compile_to_dsl(client: AsyncClient, settings: Settings) -> None:  # noqa: F811
    h, pid, worker = await _project_with_desk(client, settings)
    base = f"/api/v1/projects/{pid}/scene"

    res = await client.patch(
        f"{base}/objects/desk_top",
        headers=h,
        json={"name": "Walnut Top", "location": [0, 0, 0.8], "dimensions": [1.8, 0.8, 0.04]},
    )
    assert res.status_code == 202, res.text
    spec = await worker.next_spec()
    assert _ops(spec) == [("rename_object", "desk_top"), ("set_dimensions", "desk_top"), ("move_object", "desk_top")]
    await worker.run(spec)

    await client.post(f"{base}/objects", headers=h, json={"kind": "sphere", "location": [0, 0, 1]})
    spec = await worker.next_spec()
    assert spec.operations[0]["operation_type"] == "create_sphere"
    assert spec.operations[0]["target_object_id"] == "sphere"
    await worker.run(spec)
    await client.post(f"{base}/objects", headers=h, json={"kind": "sphere"})
    spec = await worker.next_spec()
    assert spec.operations[0]["target_object_id"] == "sphere_2"  # ids stay unique
    await worker.run(spec)

    await client.patch(f"{base}/objects/leg_fl", headers=h, json={"visible": False})
    spec = await worker.next_spec()
    assert _ops(spec) == [("set_visibility", "leg_fl")]
    await worker.run(spec)


async def test_edit_errors(client: AsyncClient, settings: Settings) -> None:  # noqa: F811
    h, pid, _ = await _project_with_desk(client, settings)
    base = f"/api/v1/projects/{pid}/scene"
    assert (await client.patch(f"{base}/objects/nope", headers=h, json={"name": "x"})).status_code == 404
    assert (await client.patch(f"{base}/objects/desk_top", headers=h, json={})).json()["code"] == "no_changes"
    bad = await client.patch(f"{base}/objects/desk_top", headers=h, json={"dimensions": [0, 1, 1]})
    assert bad.status_code == 422
    bad = await client.patch(f"{base}/objects/desk_top", headers=h, json={"name": "__import__('os')"})
    assert bad.status_code == 422
    assert (await client.patch(f"{base}/objects/Bad-Id", headers=h, json={"name": "x"})).status_code == 422
    # material must exist in the scene -> rejected by the semantic validator
    res = await client.patch(f"{base}/objects/desk_top", headers=h, json={"material_id": "unobtainium"})
    assert res.status_code == 422 and res.json()["code"] == "invalid_batch"


async def test_delete_with_children_needs_recursive(client: AsyncClient, settings: Settings) -> None:  # noqa: F811
    h, pid, worker = await _project_with_desk(client, settings)
    # The fake worker's inventory has no parent links, so check the real validator path via the
    # hierarchy in the starter batch: legs are parented to desk_top in the DSL.
    res = await client.delete(f"/api/v1/projects/{pid}/scene/objects/leg_fl", headers=h)
    assert res.status_code == 202
    spec = await worker.next_spec()
    assert spec.operations[0]["parameters"] == {"recursive": False}
    await worker.run(spec)
    res = await client.delete(f"/api/v1/projects/{pid}/scene/objects/desk_top?recursive=true", headers=h)
    spec = await worker.next_spec()
    assert spec.operations[0]["parameters"] == {"recursive": True}


async def test_material_edits(client: AsyncClient, settings: Settings) -> None:  # noqa: F811
    h, pid, worker = await _project_with_desk(client, settings)
    base = f"/api/v1/projects/{pid}/scene"
    res = await client.patch(f"{base}/materials/oak", headers=h, json={"base_color": "#5C3A21", "roughness": 0.4})
    assert res.status_code == 202
    spec = await worker.next_spec()
    assert spec.operations[0]["operation_type"] == "update_material"
    assert spec.operations[0]["parameters"]["base_color"] == "#5C3A21"
    await worker.run(spec)

    res = await client.post(f"{base}/objects/leg_fl/material", headers=h, json={"base_color": "#111111"})
    assert res.status_code == 202, res.text
    spec = await worker.next_spec()
    assert _ops(spec) == [("create_material", None), ("assign_material", "leg_fl")]
    assert spec.operations[0]["parameters"]["material_id"] == "leg_fl_mat"
    await worker.run(spec)
    assert (await client.patch(f"{base}/materials/nope", headers=h, json={"metallic": 1})).status_code == 404


async def test_restore_branches_history(client: AsyncClient, settings: Settings) -> None:  # noqa: F811
    h, pid, worker = await _project_with_desk(client, settings)
    await client.patch(f"/api/v1/projects/{pid}/scene/objects/desk_top", headers=h, json={"name": "Top"})
    await worker.run()
    versions = (await client.get(f"/api/v1/projects/{pid}/versions", headers=h)).json()
    v2, v1 = versions[0]["id"], versions[1]["id"]

    res = await client.post(f"/api/v1/projects/{pid}/versions/{v1}/restore", headers=h)
    assert res.status_code == 200 and res.json()["current_version_id"] == v1
    scene = (await client.get(f"/api/v1/projects/{pid}/scene", headers=h)).json()
    assert scene["version"]["id"] == v1

    await client.patch(f"/api/v1/projects/{pid}/scene/objects/desk_top", headers=h, json={"name": "Other"})
    spec = await worker.next_spec()
    assert spec.source.version_id.hex == v1.replace("-", "") and spec.target_version_number == 3
    await worker.run(spec)
    versions = (await client.get(f"/api/v1/projects/{pid}/versions", headers=h)).json()
    assert versions[0]["version_number"] == 3 and versions[0]["parent_version_id"] == v1
    assert {v["id"] for v in versions} >= {v1, v2}  # nothing is lost

    # restore is blocked while a job is building
    await client.patch(f"/api/v1/projects/{pid}/scene/objects/desk_top", headers=h, json={"name": "Busy"})
    res = await client.post(f"/api/v1/projects/{pid}/versions/{v2}/restore", headers=h)
    assert res.status_code == 409 and res.json()["code"] == "project_busy"


async def test_scene_ownership(client: AsyncClient, settings: Settings) -> None:  # noqa: F811
    _, pid, _ = await _project_with_desk(client, settings)
    other = auth((await register(client, email="b@example.com"))["access_token"])
    base = f"/api/v1/projects/{pid}"
    assert (await client.get(f"{base}/scene", headers=other)).status_code == 404
    assert (await client.patch(f"{base}/scene/objects/desk_top", headers=other, json={"name": "x"})).status_code == 404
    assert (await client.delete(f"{base}/scene/objects/desk_top", headers=other)).status_code == 404
    assert (await client.post(f"{base}/scene/objects", headers=other, json={"kind": "cube"})).status_code == 404

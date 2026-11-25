"""Phase 7: exports (spec §13.8), renders and texture uploads."""

from __future__ import annotations

import hashlib
import struct
import uuid
import zlib
from pathlib import Path

from httpx import AsyncClient

from app.core.config import Settings
from forma3d_shared.schemas.job import BLENDER_JOB_STREAM, BlenderJobSpec
from tests.conftest import auth, register
from tests.test_modeling_pipeline import INTERNAL, settings  # noqa: F401  (fixture reuse)
from tests.test_scene_api import _project_with_desk


def png(w: int = 8, h: int = 4) -> bytes:
    raw = b"".join(b"\x00" + b"\x80\x40\x20" * w for _ in range(h))

    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def jpeg(w: int = 640, h: int = 480) -> bytes:
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, h, w, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9 + sof + b"\xff\xd9"


async def _spec(client: AsyncClient) -> BlenderJobSpec:
    redis = client.app.state.redis  # type: ignore[attr-defined]
    entries = await redis.xrange(BLENDER_JOB_STREAM)
    assert entries, "no job queued"
    entry_id, fields = entries[-1]
    await redis.xdel(BLENDER_JOB_STREAM, entry_id)
    return BlenderJobSpec.model_validate_json(fields[b"job"])


async def _finish(
    client: AsyncClient,
    settings: Settings,  # noqa: F811
    spec: BlenderJobSpec,
    filename: str,
    data: bytes,
    *,
    fail: bool = False,
    details: dict | None = None,
) -> None:
    res = await client.post(f"/internal/v1/jobs/{spec.job_id}/start", json={"worker_id": "w"}, headers=INTERNAL)
    assert res.json()["proceed"]
    if fail:
        body = {
            "job_id": str(spec.job_id),
            "success": False,
            "error_code": "unknown_camera",
            "error_message": "camera 'x' does not exist",
        }
    else:
        staging = Path(settings.data_root) / spec.staging_dir
        staging.mkdir(parents=True)
        (staging / filename).write_bytes(data)
        body = {
            "job_id": str(spec.job_id),
            "success": True,
            "worker_id": "w",
            "artifacts": [
                {"kind": spec.kind, "filename": filename, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            ],
            "details": details or {},
        }
    res = await client.post(f"/internal/v1/jobs/{spec.job_id}/complete", json=body, headers=INTERNAL)
    assert res.status_code == 200, res.text


async def test_export_lifecycle_cache_and_download(client: AsyncClient, settings: Settings) -> None:  # noqa: F811
    h, pid, _worker = await _project_with_desk(client, settings)
    res = await client.post(f"/api/v1/projects/{pid}/exports", headers=h, json={"format": "stl", "unit": "mm"})
    assert res.status_code == 202, res.text
    export = res.json()
    assert export["status"] == "queued" and export["url"] is None
    assert export["settings"] == {"format": "stl", "unit": "mm", "include_hidden": False}
    spec = await _spec(client)
    assert spec.kind == "export" and spec.export.format == "stl" and spec.export.unit == "mm"
    assert spec.operations == [] and spec.source is not None and spec.source.version_number == 1

    # the project stays editable while an export runs (no project lock)
    res = await client.post(f"/api/v1/projects/{pid}/scene/objects", headers=h, json={"kind": "cube"})
    assert res.status_code == 202
    await _spec(client)  # drop the modeling job

    stl = b"\x00" * 80 + struct.pack("<I", 1) + b"\x00" * 50
    await _finish(client, settings, spec, "scene.stl", stl)
    done = (await client.get(f"/api/v1/exports/{export['id']}", headers=h)).json()
    assert done["status"] == "succeeded" and done["version_number"] == 1 and done["size"] == len(stl)
    dl = await client.get(done["url"], headers=h)
    assert dl.status_code == 200 and dl.content == stl
    assert dl.headers["content-type"] == "model/stl"
    assert 'filename="desk-v1-mm.stl"' in dl.headers["content-disposition"]

    # same request again: served from cache, no Blender job
    again = (
        await client.post(f"/api/v1/projects/{pid}/exports", headers=h, json={"format": "stl", "unit": "mm"})
    ).json()
    assert again["status"] == "succeeded" and again["url"] and again["id"] != export["id"]
    assert not await client.app.state.redis.xrange(BLENDER_JOB_STREAM)  # type: ignore[attr-defined]
    listed = (await client.get(f"/api/v1/projects/{pid}/exports", headers=h)).json()
    assert len(listed) == 2

    # exports never appear as version assets
    versions = (await client.get(f"/api/v1/projects/{pid}/versions", headers=h)).json()
    assert all(not a["kind"].startswith("export") for v in versions for a in v["assets"])


async def test_glb_export_is_immediate_and_formats_are_allowlisted(client: AsyncClient, settings: Settings) -> None:  # noqa: F811
    h, pid, _ = await _project_with_desk(client, settings)
    res = (await client.post(f"/api/v1/projects/{pid}/exports", headers=h, json={"format": "glb"})).json()
    assert res["status"] == "succeeded"
    dl = await client.get(res["url"], headers=h)
    assert dl.status_code == 200 and dl.content.startswith(b"glTF")
    for bad in (
        {"format": "fbx"},
        {"format": "../../etc"},
        {"format": "stl", "unit": "inch"},
        {"format": "stl", "x": 1},
    ):
        assert (await client.post(f"/api/v1/projects/{pid}/exports", headers=h, json=bad)).status_code == 422


async def test_outputs_are_owner_only(client: AsyncClient, settings: Settings) -> None:  # noqa: F811
    h, pid, _ = await _project_with_desk(client, settings)
    export = (await client.post(f"/api/v1/projects/{pid}/exports", headers=h, json={"format": "glb"})).json()
    other = auth((await register(client, "mallory@example.com"))["access_token"])
    assert (await client.get(f"/api/v1/exports/{export['id']}", headers=other)).status_code == 404
    assert (await client.get(export["url"], headers=other)).status_code == 404
    assert (
        await client.post(f"/api/v1/projects/{pid}/exports", headers=other, json={"format": "glb"})
    ).status_code == 404
    assert (await client.post(f"/api/v1/projects/{pid}/renders", headers=other, json={})).status_code == 404
    # an export id is not a render id
    assert (await client.get(f"/api/v1/renders/{export['id']}", headers=h)).status_code == 404


async def test_render_lifecycle_and_rules(client: AsyncClient, settings: Settings) -> None:  # noqa: F811
    h, pid, _ = await _project_with_desk(client, settings)
    base = f"/api/v1/projects/{pid}/renders"
    res = await client.post(base, headers=h, json={"engine": "eevee"})
    assert res.status_code == 422 and res.json()["code"] == "engine_unavailable"
    res = await client.post(base, headers=h, json={"width": 9000})
    assert res.status_code == 422
    res = await client.post(base, headers=h, json={"camera_object_id": "desk_top"})
    assert res.status_code == 422 and res.json()["code"] == "unknown_camera"

    res = await client.post(base, headers=h, json={"quality": "high", "width": 800, "height": 600})
    assert res.status_code == 202, res.text
    render = res.json()
    spec = await _spec(client)
    assert spec.kind == "render" and spec.render.quality == "high" and spec.render.width == 800
    assert spec.timeout_seconds == settings.render_timeout_seconds
    await _finish(client, settings, spec, "render.png", png(), details={"samples": 256, "camera_object_id": None})
    done = (await client.get(f"/api/v1/renders/{render['id']}", headers=h)).json()
    assert done["status"] == "succeeded" and done["details"]["samples"] == 256
    img = await client.get(done["url"], headers=h)
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"

    # failed render: reported, nothing stored
    res = (await client.post(base, headers=h, json={"quality": "draft"})).json()
    await _finish(client, settings, await _spec(client), "", b"", fail=True)
    failed = (await client.get(f"/api/v1/renders/{res['id']}", headers=h)).json()
    assert failed["status"] == "failed" and "camera" in failed["error_message"] and failed["url"] is None
    assert len((await client.get(base, headers=h)).json()) == 2


async def test_pending_output_limit(client: AsyncClient, settings: Settings) -> None:  # noqa: F811
    h, pid, _ = await _project_with_desk(client, settings)
    for q in ("draft", "standard", "high"):
        assert (await client.post(f"/api/v1/projects/{pid}/renders", headers=h, json={"quality": q})).status_code == 202
    assert (await client.post(f"/api/v1/projects/{pid}/exports", headers=h, json={"format": "obj"})).status_code == 202
    res = await client.post(f"/api/v1/projects/{pid}/exports", headers=h, json={"format": "gltf"})
    assert res.status_code == 409 and res.json()["code"] == "busy"


async def test_texture_upload_and_use_in_batches(client: AsyncClient, settings: Settings) -> None:  # noqa: F811
    h, pid, worker = await _project_with_desk(client, settings)
    url = f"/api/v1/projects/{pid}/textures"
    res = await client.post(url, headers=h, files={"file": ("wood grain.png", png(), "image/png")})
    assert res.status_code == 201, res.text
    tex = res.json()
    assert (tex["format"], tex["width"], tex["height"], tex["name"]) == ("png", 8, 4, "wood grain.png")
    res = await client.post(url, headers=h, files={"file": ("photo.jpg", jpeg(), "image/jpeg")})
    assert res.status_code == 201 and (res.json()["width"], res.json()["height"]) == (640, 480)
    for name, data in (("x.png", b"not an image"), ("x.gif", b"GIF89a" + b"\x00" * 20), ("big.png", png(5000, 1))):
        bad = await client.post(url, headers=h, files={"file": (name, data, "image/png")})
        assert bad.status_code in (413, 422), (name, bad.text)
    assert len((await client.get(url, headers=h)).json()) == 2
    assert (await client.get(tex["url"], headers=h)).content == png()

    # a batch referencing the upload carries a verified copy reference to the worker
    batch = {
        "schema_version": "1.0",
        "operations": [
            {
                "operation_id": "t",
                "operation_type": "assign_texture",
                "parameters": {"material_id": "oak", "image_id": tex["id"]},
            }
        ],
    }
    res = await client.post(f"/api/v1/projects/{pid}/modeling/execute", headers=h, json=batch)
    assert res.status_code == 202, res.text
    spec = await worker.next_spec()
    assert [str(t.image_id) for t in spec.textures] == [tex["id"]]
    assert spec.textures[0].sha256 == hashlib.sha256(png()).hexdigest()
    await worker.run(spec)

    # unknown or foreign image ids are rejected before Blender
    batch["operations"][0]["parameters"]["image_id"] = str(uuid.uuid4())
    res = await client.post(f"/api/v1/projects/{pid}/modeling/execute", headers=h, json=batch)
    assert res.status_code == 422 and res.json()["issues"][0]["code"] == "unknown_image"
    other = auth((await register(client, "eve@example.com"))["access_token"])
    assert (await client.get(tex["url"], headers=other)).status_code == 404


async def test_running_render_does_not_block_chat_or_edits(client: AsyncClient, settings: Settings) -> None:  # noqa: F811
    h, pid, _ = await _project_with_desk(client, settings)
    assert (await client.post(f"/api/v1/projects/{pid}/renders", headers=h, json={})).status_code == 202
    scene = (await client.get(f"/api/v1/projects/{pid}/scene", headers=h)).json()
    assert scene["active_job"] is None
    res = await client.post(f"/api/v1/projects/{pid}/conversation/messages", headers=h, json={"content": "Add a lamp"})
    assert res.status_code == 202, res.text


async def test_chat_export_and_render_start_output_jobs(client: AsyncClient, settings: Settings) -> None:  # noqa: F811
    from tests.test_conversations import _agent_spec, _complete, _start

    h, pid, _ = await _project_with_desk(client, settings)
    res = await client.post(
        f"/api/v1/projects/{pid}/conversation/messages", headers=h, json={"content": "Export it as STL"}
    )
    assert res.status_code == 202
    spec = await _agent_spec(client)
    await _start(client, spec.job_id)
    await _complete(
        client,
        spec.job_id,
        success=True,
        intent="export",
        reply="Preparing your STL.",
        metadata={"export_format": "stl"},
    )
    msg = (await client.get(f"/api/v1/projects/{pid}/conversation", headers=h)).json()["messages"][-1]
    meta = msg["metadata"]
    assert meta["action"] == "export" and meta["export_format"] == "stl" and meta["export_version_number"] == 1
    export = (await client.get(f"/api/v1/exports/{meta['output_job_id']}", headers=h)).json()
    assert export["status"] == "queued" and export["settings"]["format"] == "stl"
    out_spec = await _spec(client)
    assert out_spec.kind == "export" and out_spec.source.version_id == uuid.UUID(meta["export_version_id"])

    await client.post(f"/api/v1/projects/{pid}/conversation/messages", headers=h, json={"content": "Render it"})
    spec = await _agent_spec(client)
    await _start(client, spec.job_id)
    await _complete(
        client, spec.job_id, success=True, intent="render", reply="Rendering.", metadata={"render_quality": "bogus"}
    )
    meta = (await client.get(f"/api/v1/projects/{pid}/conversation", headers=h)).json()["messages"][-1]["metadata"]
    assert meta["action"] == "render" and meta["render_quality"] == "standard"
    render = (await client.get(f"/api/v1/renders/{meta['output_job_id']}", headers=h)).json()
    assert render["settings"]["quality"] == "standard" and render["settings"]["width"] == 1280


async def test_direct_edits_for_lights_cameras_and_material_looks(client: AsyncClient, settings: Settings) -> None:  # noqa: F811
    h, pid, worker = await _project_with_desk(client, settings)
    base = f"/api/v1/projects/{pid}/scene"

    res = await client.post(f"{base}/objects", headers=h, json={"kind": "light"})
    assert res.status_code == 202, res.text
    spec = await worker.next_spec()
    op = spec.operations[0]
    assert op["operation_type"] == "add_light" and op["parameters"]["location"] == [1.5, -1.5, 2.5]
    await worker.run(spec)

    res = await client.post(f"{base}/objects", headers=h, json={"kind": "camera", "location": [0, -4, 1.5]})
    spec = await worker.next_spec()
    assert spec.operations[0]["operation_type"] == "add_camera"
    assert spec.operations[0]["parameters"]["look_at"] == [0.0, 0.0, 0.5]
    await worker.run(spec)

    res = await client.patch(f"{base}/objects/light", headers=h, json={"light_energy": 800, "light_color": "#FFE0B0"})
    assert res.status_code == 202, res.text
    spec = await worker.next_spec()
    assert [o["operation_type"] for o in spec.operations] == ["update_light"]
    assert {k: v for k, v in spec.operations[0]["parameters"].items() if v is not None} == {
        "energy": 800.0,
        "color": "#FFE0B0",
    }
    await worker.run(spec)

    res = await client.patch(
        f"{base}/materials/oak", headers=h, json={"texture": "wood", "texture_scale": 2.0, "transmission": 0.0}
    )
    assert res.status_code == 202, res.text
    spec = await worker.next_spec()
    assert [o["operation_type"] for o in spec.operations] == ["update_material", "assign_texture"]
    p = {k: v for k, v in spec.operations[1]["parameters"].items() if v is not None}
    assert p == {"material_id": "oak", "pattern": "wood", "scale": 2.0, "rotation": 0.0}
    await worker.run(spec)
    res = await client.patch(f"{base}/materials/oak", headers=h, json={"texture": "lava"})
    assert res.status_code == 422

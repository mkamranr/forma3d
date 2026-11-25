"""Image-to-3D: reference photo upload, attaching photos to a chat message, and the agent's access to them."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient

from app.core.config import Settings
from tests.test_conversations import _agent_spec, _complete, _post, _start
from tests.test_modeling_pipeline import INTERNAL, _setup, pipeline_settings
from tests.test_outputs import jpeg, png


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return pipeline_settings(tmp_path)


async def _upload(client: AsyncClient, h: dict, pid: str, name: str = "chair.png", data: bytes | None = None):
    return await client.post(
        f"/api/v1/projects/{pid}/references", headers=h, files={"file": (name, data or png(), "image/png")}
    )


async def test_photo_message_reaches_the_agent_and_keeps_its_description(client: AsyncClient, settings: Settings):
    h, pid = await _setup(client)
    res = await _upload(client, h, pid, "my chair.jpg", jpeg(800, 600))
    assert res.status_code == 201, res.text
    ref = res.json()
    assert ref["width"] == 800 and ref["format"] == "jpeg"
    assert ref["url"] == f"/api/v1/projects/{pid}/references/{ref['id']}"
    img = await client.get(ref["url"], headers=h)
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
    # references are not textures
    assert (await client.get(f"/api/v1/projects/{pid}/textures", headers=h)).json() == []

    # A photo alone is a valid message.
    out = await _post(client, h, pid, "", attachment_ids=[ref["id"]])
    msg = out["message"]
    assert msg["content"] == "Build this object from the attached photo."
    assert msg["metadata"]["attachments"] == [{"id": ref["id"], "name": "my chair.jpg", "width": 800, "height": 600}]

    spec = await _agent_spec(client)
    await _start(client, spec.job_id)
    ctx = (await client.get(f"/internal/v1/agent-jobs/{spec.job_id}/context", headers=INTERNAL)).json()
    assert ctx["reference_images"] == [
        {"id": ref["id"], "name": "my chair.jpg", "media_type": "image/jpeg", "width": 800, "height": 600}
    ]
    assert ctx["reference_description"] == ""
    data = await client.get(f"/internal/v1/agent-jobs/{spec.job_id}/references/{ref['id']}", headers=INTERNAL)
    assert data.status_code == 200 and data.content == jpeg(800, 600)
    # the worker may only read photos attached to its own job
    other = (await _upload(client, h, pid, "other.png")).json()
    res = await client.get(f"/internal/v1/agent-jobs/{spec.job_id}/references/{other['id']}", headers=INTERNAL)
    assert res.status_code == 404

    described = "office chair, about 0.65 × 0.65 × 1 m (W × D × H).\nParts:\n- seat: box"
    res = await client.post(
        f"/internal/v1/agent-jobs/{spec.job_id}/reference-description",
        json={"description": described},
        headers=INTERNAL,
    )
    assert res.status_code == 204
    ctx = (await client.get(f"/internal/v1/agent-jobs/{spec.job_id}/context", headers=INTERNAL)).json()
    assert ctx["reference_description"] == described  # a resumed workflow skips the vision model
    await _complete(client, spec.job_id, success=True, intent="question", reply="Nice chair.")

    conv = (await client.get(f"/api/v1/projects/{pid}/conversation", headers=h)).json()
    assert conv["messages"][0]["metadata"]["reference_description"] == described

    # Later turns see the description in their history.
    await _post(client, h, pid, "make it red")
    spec2 = await _agent_spec(client)
    ctx2 = (await client.get(f"/internal/v1/agent-jobs/{spec2.job_id}/context", headers=INTERNAL)).json()
    assert ctx2["reference_images"] == []
    assert "Attached photo, as read by the vision model: office chair" in ctx2["history"][0]["content"]


async def test_attachment_rules(client: AsyncClient, settings: Settings):
    h, pid = await _setup(client)
    hb, pid_b = await _setup(client, email="b@example.com")
    foreign = (await _upload(client, hb, pid_b)).json()["id"]
    url = f"/api/v1/projects/{pid}/conversation/messages"

    # nothing at all
    assert (await client.post(url, json={"content": "", "attachment_ids": []}, headers=h)).status_code == 422
    # another user's photo, an unknown id, a texture: all "not found"
    tex = await client.post(
        f"/api/v1/projects/{pid}/textures", headers=h, files={"file": ("t.png", png(), "image/png")}
    )
    for rid in (foreign, str(uuid.uuid4()), tex.json()["id"]):
        res = await client.post(url, json={"content": "build", "attachment_ids": [rid]}, headers=h)
        assert res.status_code == 422 and res.json()["code"] == "attachment_not_found", rid
    # at most 4
    ids = [(await _upload(client, h, pid, f"{i}.png")).json()["id"] for i in range(5)]
    assert (await client.post(url, json={"content": "x", "attachment_ids": ids}, headers=h)).status_code == 422
    # other users cannot read the photo
    assert (await client.get(f"/api/v1/projects/{pid_b}/references/{foreign}", headers=h)).status_code == 404
    # not an image
    res = await _upload(client, h, pid, "doc.png", b"%PDF-1.7 nope")
    assert res.status_code == 422 and res.json()["code"] == "invalid_image"

"""Image-to-3D: a photo attached to a message is described by the Vision Agent (VLM) and built through
the normal conversation -> planning -> Blender -> QA path."""

from __future__ import annotations

import json

import fakeredis
import httpx
import pytest

from app.agents.vision import Description
from app.providers.base import ChatMessage, LLMError
from app.providers.fake import FakeProvider
from app.providers.openai_compatible import OpenAICompatibleProvider

from .sim import SimBackend
from .test_workflow import CONV, PID, agents_run, events, make_worker

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff" + b"\x00" * 32


@pytest.fixture
def r():
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def sim(r):  # noqa: ANN001
    return SimBackend(r)


def send(worker, sim, text, images):  # noqa: ANN001
    import uuid

    from forma3d_shared.schemas.agent import AgentJobSpec

    jid = sim.post(text, images=images)
    worker.runner.run(AgentJobSpec(job_id=jid, project_id=PID, conversation_id=CONV, message_id=uuid.uuid4()))
    return jid, sim.completed.get(jid)


def test_photo_only_message_builds_the_described_object(sim, r):  # noqa: ANN001
    fake = FakeProvider()
    w = make_worker(sim, {"conversation": fake, "planning": fake, "revision": fake, "vision": fake})
    jid, done = send(w, sim, "Build this object from the attached photo.", [PNG])
    assert done is not None and done.success, done
    assert fake.images_seen == 1
    assert "vision" in agents_run(sim, jid)
    description = sim.jobs[jid]["description"]
    assert description.startswith("modern wooden desk")
    assert "Parts:" in description and "oak (#C99563, satin, wood texture)" in description
    # the description reached the planner, and the desk was built
    planning_prompt = next(m for role, m in fake.calls if role == "planning")[-1].content
    assert "<reference_image>modern wooden desk" in planning_prompt
    assert "desk_top" in sim.objects()
    stages = [e["stage"] for e in events(r, jid)]
    assert stages.index("vision") < stages.index("understanding")


def test_words_with_the_photo_steer_the_result(sim):  # noqa: ANN001
    w = make_worker(sim)  # no dedicated vision model: falls back to the conversation provider
    _, done = send(w, sim, "Build this chair", [JPEG])
    assert done is not None and done.success
    assert any("seat" in i or "chair" in i for i in sim.objects())


def test_photo_without_an_object_is_explained_and_nothing_is_built(sim):  # noqa: ANN001
    w = make_worker(sim)
    _, done = send(w, sim, "blank", [PNG])
    assert done is not None and not done.success
    assert done.error_code == "no_object_in_image"
    assert "couldn't find an object" in done.reply
    assert sim.versions == []


class TextOnlyModel(FakeProvider):
    """A model server that rejects image input (no vision support)."""

    def generate_structured(self, messages, schema, *, max_tokens=None):  # noqa: ANN001
        if any(m.images for m in messages):
            raise LLMError("LLM rejected the request (400): images are not supported", retryable=False)
        return super().generate_structured(messages, schema, max_tokens=max_tokens)


def test_model_without_vision_gives_a_clear_reply(sim):  # noqa: ANN001
    w = make_worker(sim, TextOnlyModel())
    _, done = send(w, sim, "Build this", [PNG])
    assert done is not None and not done.success
    assert done.error_code == "vision_failed"
    assert "LLM_MODEL_VISION" in done.reply
    assert sim.versions == []


def test_existing_description_is_reused_without_calling_the_vlm(sim):  # noqa: ANN001
    fake = FakeProvider()
    w = make_worker(sim, {"conversation": fake, "planning": fake, "revision": fake, "vision": fake})
    import uuid

    from forma3d_shared.schemas.agent import AgentJobSpec

    jid = sim.post("Build this object from the attached photo.", images=[PNG])
    sim.jobs[jid]["description"] = "modern wooden desk (modern), about 1.6 × 0.8 × 0.75 m (W × D × H)."
    w.runner.run(AgentJobSpec(job_id=jid, project_id=PID, conversation_id=CONV, message_id=uuid.uuid4()))
    assert sim.completed[jid].success
    assert getattr(sim, "reference_reads", 0) == 0
    assert all(role != "vision" for role, _ in fake.calls)


def test_description_text_rendering():
    d = Description.model_validate(
        {
            "object": "bar stool",
            "style": "industrial",
            "overall_size_m": {"width": 0.4, "depth": 0.4, "height": 0.75},
            "parts": [
                {
                    "name": "seat",
                    "shape": "cylinder",
                    "size_m": {"width": 0.36, "depth": 0.36, "height": 0.04},
                    "position": "on top",
                    "material": "walnut",
                },
                {
                    "name": "leg",
                    "shape": "cylinder",
                    "count": 4,
                    "size_m": {"width": 0.025, "depth": 0.025, "height": 0.72},
                    "position": "corners",
                    "orientation": "splayed ~8° outward",
                    "material": "black steel",
                },
            ],
            "materials": [{"name": "black steel", "color": "#1A1A1A", "finish": "metallic", "texture": "none"}],
            "confidence": "low",
        }
    )
    text = d.as_text()
    assert text.splitlines()[0] == "bar stool (industrial), about 0.4 × 0.4 × 0.75 m (W × D × H)."
    assert "- 4 × leg: cylinder, 0.025 × 0.025 × 0.72 m, corners, splayed ~8° outward, black steel" in text
    assert "black steel (#1A1A1A, metallic)" in text
    assert "rough guesses" in text


def test_openai_provider_sends_images_as_content_parts():
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(json.loads(req.content))
        return httpx.Response(200, json={"model": "m", "choices": [{"message": {"content": "{}"}}]})

    client = httpx.Client(base_url="http://llm/v1", transport=httpx.MockTransport(handler))
    p = OpenAICompatibleProvider("http://llm/v1", "m", client=client)
    p.generate_structured(
        [ChatMessage("system", "s"), ChatMessage("user", "describe", images=["data:image/png;base64,AAAA"])],
        {"type": "object"},
    )
    system, user = seen["messages"]
    assert system == {"role": "system", "content": "s"}
    assert user["content"] == [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        {"type": "text", "text": "describe"},
    ]

"""Unit tests of the deterministic agents: inspection, modeling compiler, QA and plan merging."""

from __future__ import annotations

import pytest
from forma3d_shared.constants.limits import ModelingLimits
from forma3d_shared.schemas.scene_object import SceneInventory
from forma3d_shared.validation import validate_batch

from app.agents.conversation import Decision, make_check
from app.agents.inspection import scene_report, world_box
from app.agents.modeling import compile_plan
from app.agents.plan import Plan, merge_revision
from app.agents.planning import plan_problems
from app.agents.quality import run_qa
from app.providers.fake import desk_plan

from .sim import simulate

EMPTY = SceneInventory()


def build(inv: SceneInventory, plan: Plan):  # noqa: ANN201
    compiled = compile_plan(plan, inv)
    validate_batch(compiled.batch(), inv, ModelingLimits())
    after = SceneInventory.model_validate(simulate(inv.model_dump(mode="json"), compiled.operations))
    return compiled, after


@pytest.fixture
def desk():
    _c, after = build(EMPTY, Plan.model_validate(desk_plan()))
    return after


def drawers_plan(z: float) -> Plan:
    parts, relations = [], []
    for did, x in (("drawer_left", -0.4), ("drawer_right", 0.4)):
        parts += [
            {
                "id": did,
                "action": "create",
                "name": did,
                "component": "drawer",
                "shape": "box",
                "size": [0.5, 0.55, 0.12],
                "position": [x, 0, z],
                "material": "oak",
                "parent": "desk_top",
            },
            {
                "id": f"{did}_handle",
                "action": "create",
                "name": "h",
                "component": "handle",
                "shape": "box",
                "size": [0.12, 0.02, 0.02],
                "position": [x, -0.285, z],
                "material": "oak",
                "parent": did,
            },
        ]
        relations += [
            {"subject": did, "relation": "under", "object": "desk_top"},
            {"subject": f"{did}_handle", "relation": "attached_to", "object": did},
        ]
    return Plan.model_validate({"summary": "drawers", "reply": "ok", "parts": parts, "relations": relations})


def test_desk_compiles_validates_and_passes_qa(desk):  # noqa: ANN001
    compiled, after = build(EMPTY, Plan.model_validate(desk_plan()))
    ops = [o["operation_type"] for o in compiled.operations]
    assert ops[:2] == ["create_material", "create_material"] and ops.count("create_cube") == 5
    assert set(compiled.expectations.created) == {"desk_top", "leg_fl", "leg_fr", "leg_bl", "leg_br"}
    report = run_qa(EMPTY, after, compiled.expectations)
    assert report.passed, [i.message for i in report.issues]
    assert report.checks > 10


def test_world_box_rotation():
    box = world_box((0, 0, 0), (2.0, 0.2, 0.2), (0, 0, 90))
    assert box.size[0] == pytest.approx(0.2) and box.size[1] == pytest.approx(2.0)


def test_scene_report_lists_bounds_and_material_users(desk):  # noqa: ANN001
    rep = scene_report(desk)
    top = next(o for o in rep["objects"] if o["id"] == "desk_top")
    assert top["bounds"]["min"][2] == pytest.approx(0.72)
    graphite = next(m for m in rep["materials"] if m["id"] == "graphite")
    assert sorted(graphite["used_by"]) == ["leg_bl", "leg_br", "leg_fl", "leg_fr"]


def test_qa_detects_drawer_gap_with_fix_hint(desk):  # noqa: ANN001
    compiled, after = build(desk, drawers_plan(z=0.56))  # 10 cm too low
    report = run_qa(desk, after, compiled.expectations)
    assert not report.passed
    gap = next(i for i in report.fixable if i.object_id == "drawer_left")
    assert gap.data["suggested_position"][2] == pytest.approx(0.66)
    _c2, fixed = build(desk, drawers_plan(z=0.66))
    assert run_qa(desk, fixed, _c2.expectations).passed


def test_qa_detects_change_to_untouched_object(desk):  # noqa: ANN001
    compiled, _after = build(
        desk, Plan.model_validate({"parts": [{"id": "desk_top", "action": "modify", "material": "graphite"}]})
    )
    tampered = desk.model_dump(mode="json")
    for o in tampered["objects"]:
        if o["object_id"] == "desk_top":
            o["material_ids"] = ["graphite"]
        if o["object_id"] == "leg_fl":
            o["location"][0] += 0.3  # a leg moved although the plan didn't touch it
    report = run_qa(desk, SceneInventory.model_validate(tampered), compiled.expectations)
    assert any(i.object_id == "leg_fl" and i.severity == "error" for i in report.issues)


def test_plan_problems_are_actionable(desk):  # noqa: ANN001
    plan = Plan.model_validate(
        {
            "parts": [
                {"id": "desk_top", "action": "create", "shape": "box", "size": [1, 1, 1], "position": [0, 0, 0]},
                {"id": "ghost", "action": "modify", "material": "unobtainium"},
            ]
        }
    )
    problems = plan_problems(plan, desk, ModelingLimits())
    text = " | ".join(problems)
    assert "already exists" in text and "does not exist" in text and "unobtainium" in text


def test_merge_revision_patches_and_moves_children():
    plan = drawers_plan(z=0.56)
    patch = Plan.model_validate(
        {
            "parts": [
                {"id": "drawer_left", "action": "modify", "position": [-0.4, 0, 0.66]},
                {"id": "drawer_right_handle", "action": "delete"},
            ]
        }
    )
    merged = merge_revision(plan, patch)
    parts = {p.id: p for p in merged.parts}
    assert parts["drawer_left"].position == [-0.4, 0, 0.66] and parts["drawer_left"].action == "create"
    assert parts["drawer_left_handle"].position[2] == pytest.approx(0.66)  # followed its parent
    assert parts["drawer_right"].position[2] == pytest.approx(0.56)  # untouched
    assert "drawer_right_handle" not in parts and len(merged.relations) == len(plan.relations)


def test_conversation_check_rejects_unknown_targets(desk):  # noqa: ANN001
    import uuid

    from forma3d_shared.schemas.agent import AgentContext

    ctx = AgentContext(
        job_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        project_name="p",
        inventory=desk,
        user_message="x",
        version_numbers=[1],
    )
    check = make_check(ctx)
    assert check(Decision(intent="modify", task="t", targets=["desk_top"])) == []
    assert check(Decision(intent="modify", task="t", targets=["sofa"]))
    assert check(Decision(intent="restore", version_number=7))
    assert check(Decision(intent="clarify"))


def test_qa_flags_child_left_floating_by_parent_resize(desk):  # noqa: ANN001
    _c, with_drawers = build(desk, drawers_plan(z=0.66))
    shrink = Plan.model_validate({"parts": [{"id": "drawer_left", "action": "modify", "size": [0.5, 0.4, 0.12]}]})
    compiled, after = build(with_drawers, shrink)
    report = run_qa(with_drawers, after, compiled.expectations)
    issue = next(i for i in report.fixable if i.object_id == "drawer_left_handle")
    assert issue.code == "floating"
    # the hint moves the handle back against the drawer's (new) front face, not onto the floor
    assert issue.data["suggested_position"][1] == pytest.approx(-0.21) and issue.data["suggested_position"][2] == 0.66

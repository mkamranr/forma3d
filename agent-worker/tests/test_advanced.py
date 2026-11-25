"""Phase 7: the agents use the advanced modeling features — storage cabinet and office chair (MVP
scenarios), boolean holes, lights, glass and textures — and route exports/renders with a format."""

from __future__ import annotations

import fakeredis
import pytest
from forma3d_shared.schemas.scene_object import SceneInventory
from forma3d_shared.validation import validate_batch

from app.agents.inspection import local_rotation, world_rotations
from app.agents.modeling import PlanError, check_plan, compile_plan
from app.agents.plan import Plan
from app.agents.quality import run_qa

from .sim import SimBackend
from .test_workflow import make_worker, say


@pytest.fixture
def sim():
    return SimBackend(fakeredis.FakeRedis(decode_responses=True))


def mats(sim) -> dict:  # noqa: ANN001
    return {m["material_id"]: m for m in sim.inventory["materials"]}


def test_storage_cabinet_scenario(sim):  # noqa: ANN001
    w = make_worker(sim)
    _, done = say(w, sim, "Create a storage cabinet")
    assert done.success and done.metadata["quality"]["passed"], done
    objs = sim.objects()
    assert {"cabinet_body", "door_left", "door_right", "door_left_handle", "door_right_handle"} <= set(objs)
    assert mats(sim)["cabinet_oak"]["texture"]["pattern"] == "wood"
    assert objs["door_left_handle"]["parent_id"] == "door_left"


def test_office_chair_scenario(sim):  # noqa: ANN001
    w = make_worker(sim)
    _, done = say(w, sim, "Create an office chair")
    assert done.success and done.metadata["quality"]["passed"], done.metadata
    objs = sim.objects()
    assert sum(1 for o in objs.values() if o["component_type"] == "caster") == 5
    assert objs["star_leg_3"]["rotation"][2] % 360 == pytest.approx(234)
    # casters are unrotated in the world although their legs are rotated (local rotation = -leg)
    inv = SceneInventory.model_validate(sim.inventory)
    world = world_rotations(inv)
    assert all(abs(a) < 1e-6 for a in world["caster_2"]) and inv.object_map()["caster_2"].rotation[
        2
    ] % 360 == pytest.approx(198)
    assert mats(sim)["mesh_fabric"]["texture"]["pattern"] == "fabric"


def test_cable_hole_light_glass_texture(sim):  # noqa: ANN001
    w = make_worker(sim)
    say(w, sim, "Create a modern wooden desk")
    before = sim.objects()

    _, done = say(w, sim, "Drill a cable hole in the desk top")
    assert done.success and done.metadata["quality"]["passed"], done.metadata
    objs = sim.objects()
    assert "cable_hole_cutter" not in objs  # the cutting tool is consumed
    assert objs["desk_top"]["mesh"]["vertices"] > before["desk_top"]["mesh"]["vertices"]
    plan_ops = [o["operation_type"] for o in sim.batches[-1]["operations"]]
    assert plan_ops == ["create_cylinder", "boolean_difference"]

    _, done = say(w, sim, "Add a warm light above the desk")
    assert done.success and done.metadata["quality"]["passed"], done.metadata  # lights may float
    light = sim.objects()["key_light"]
    assert light["type"] == "LIGHT" and light["light"]["color"] == "#FFD9A8"

    _, done = say(w, sim, "Make desk_top glass")
    assert done.success and mats(sim)["clear_glass"]["transmission"] == 1.0
    assert sim.objects()["desk_top"]["material_ids"] == ["clear_glass"]

    _, done = say(w, sim, "Give leg_fl a brick texture")
    assert done.success
    assert mats(sim)["graphite"]["texture"]["pattern"] == "brick"


def test_export_formats_and_render_intent(sim):  # noqa: ANN001
    w = make_worker(sim)
    say(w, sim, "Create a modern wooden desk")
    _, done = say(w, sim, "Export it as STL for 3D printing")
    assert done.intent == "export" and done.metadata["export_format"] == "stl"
    _, done = say(w, sim, "Download the model")
    assert done.metadata["export_format"] == "glb"
    _, done = say(w, sim, "Render a high quality image of the desk")
    assert done.intent == "render" and done.metadata["render_quality"] == "high"
    assert len(sim.versions) == 1  # neither changes the scene


def test_compiler_rules_for_cutters_and_lights():
    inv = SceneInventory.model_validate(
        {
            "objects": [
                {
                    "object_id": "panel",
                    "name": "Panel",
                    "dimensions": [1, 1, 0.02],
                    "location": [0, 0, 0.01],
                    "mesh": {"vertices": 8},
                }
            ],
            "materials": [],
        }
    )
    plan = Plan.model_validate(
        {
            "summary": "x",
            "parts": [
                {
                    "id": "slot",
                    "action": "create",
                    "shape": "box",
                    "size": [0.2, 0.05, 0.04],
                    "position": [0, 0, 0.01],
                    "cut_into": "panel",
                    "parent": "panel",
                },
                {"id": "lamp", "action": "create", "shape": "light", "light_type": "spot", "position": [0, 0, 2]},
            ],
        }
    )
    compiled = compile_plan(plan, inv)
    ops = {o["operation_type"]: o for o in compiled.operations}
    assert "parent_id" not in ops["create_cube"]["parameters"]  # cutters are never parented
    assert ops["boolean_difference"]["parameters"] == {"operand_id": "slot"}
    assert ops["add_light"]["parameters"]["look_at"] == [0, 0, 0.0]  # spots point down by default
    assert compiled.expectations.deleted == {"slot"}
    validate_batch(compiled.batch(), inv)  # the compiled DSL is valid

    bad = Plan.model_validate(
        {
            "parts": [
                {
                    "id": "c",
                    "action": "create",
                    "shape": "box",
                    "size": [1, 1, 1],
                    "position": [0, 0, 0],
                    "cut_into": "ghost",
                }
            ]
        }
    )
    with pytest.raises(PlanError, match="cut_into"):
        compile_plan(bad, inv)


def test_qa_ignores_lights_for_support():
    before = SceneInventory()
    after = SceneInventory.model_validate(
        {
            "objects": [
                {
                    "object_id": "box",
                    "name": "Box",
                    "dimensions": [1, 1, 1],
                    "location": [0, 0, 0.5],
                    "mesh": {"vertices": 8},
                    "material_ids": ["m"],
                },
                {"object_id": "sun", "name": "Sun", "type": "LIGHT", "location": [3, 3, 5]},
            ]
        }
    )
    from app.agents.modeling import Expectations

    exp = Expectations(created={"box": {"position": [0, 0, 0.5]}, "sun": {"position": [3, 3, 5]}})
    report = run_qa(before, after, exp)
    assert report.passed, report.issues


def test_modify_rotation_is_world_space_under_a_rotated_parent():
    inv = SceneInventory.model_validate(
        {
            "objects": [
                {
                    "object_id": "base",
                    "name": "Base",
                    "rotation": [0, 0, 90],
                    "dimensions": [1, 1, 1],
                    "mesh": {"vertices": 8},
                },
                {
                    "object_id": "arm",
                    "name": "Arm",
                    "parent_id": "base",
                    "rotation": [0, 0, -90],
                    "dimensions": [1, 0.1, 0.1],
                    "mesh": {"vertices": 8},
                },
            ]
        }
    )
    assert world_rotations(inv)["arm"] == pytest.approx((0, 0, 0))
    plan = Plan.model_validate({"parts": [{"id": "arm", "action": "modify", "rotation": [0, 0, 45]}]})
    rot = next(o for o in compile_plan(plan, inv).operations if o["operation_type"] == "rotate_object")
    assert rot["parameters"]["rotation"] == pytest.approx([0, 0, -45])  # 45 in the world = -45 under a 90 parent
    assert local_rotation((90, 0, 0), (0, 0, 0)) == pytest.approx((90, 0, 0))


def test_light_without_position_goes_above_the_model():
    inv = SceneInventory.model_validate(
        {
            "objects": [
                {
                    "object_id": "top",
                    "name": "Top",
                    "dimensions": [2, 1, 0.04],
                    "location": [1, 0, 0.74],
                    "mesh": {"vertices": 8},
                }
            ]
        }
    )
    plan = Plan.model_validate({"parts": [{"id": "lamp", "action": "create", "shape": "light", "light_type": "area"}]})
    assert check_plan(plan, inv) == []  # the planning agent's pre-check accepts it too
    light = next(o for o in compile_plan(plan, inv).operations if o["operation_type"] == "add_light")
    assert light["parameters"]["location"] == pytest.approx([1.0, 0.0, 1.96])
    assert light["parameters"]["look_at"] == pytest.approx([1.0, 0.0, 0.0])

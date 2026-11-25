"""Phase 6: conversational editing — spec Scenarios A and B, selection context, undo/redo, and the
guarantee that objects a request is not about stay exactly as they were."""

from __future__ import annotations

import fakeredis
import pytest
from forma3d_shared.inventory_diff import diff_inventories
from forma3d_shared.schemas.scene_object import SceneInventory

from .sim import SimBackend
from .test_workflow import make_worker, say


@pytest.fixture
def sim():
    return SimBackend(fakeredis.FakeRedis(decode_responses=True))


def changed_ids(before: dict, after: dict) -> set[str]:
    d = diff_inventories(SceneInventory.model_validate(before), SceneInventory.model_validate(after))
    o = d["objects"]
    return {x["object_id"] for x in o["added"] + o["removed"] + o["changed"]}


def bottom(o: dict) -> float:
    return o["location"][2] - o["dimensions"][2] / 2


def test_scenario_a_desk_material_dimensions_drawer_export(sim):  # noqa: ANN001
    w = make_worker(sim)
    _, done = say(w, sim, "Create a modern wooden desk")
    assert done.success
    v1 = sim.inventory

    _, done = say(w, sim, "Change the top to walnut")
    assert done.success and changed_ids(v1, sim.inventory) == {"desk_top"}
    v2 = sim.inventory

    _, done = say(w, sim, "Make the desk 20 cm wider")
    assert done.success and done.metadata["quality"]["passed"], done
    objs = sim.objects()
    assert objs["desk_top"]["dimensions"][0] == pytest.approx(1.8)
    assert objs["leg_fr"]["location"][0] == pytest.approx(0.85) and objs["leg_fl"]["location"][0] == pytest.approx(
        -0.85
    )
    assert objs["desk_top"]["material_ids"] == ["walnut"]  # earlier change kept
    assert changed_ids(v2, sim.inventory) == {"desk_top", "leg_fl", "leg_fr", "leg_bl", "leg_br"}
    v3 = sim.inventory

    _, done = say(w, sim, "Add two drawers")
    assert done.success
    assert changed_ids(v3, sim.inventory) == {
        "drawer_left",
        "drawer_right",
        "drawer_left_handle",
        "drawer_right_handle",
    }

    _, done = say(w, sim, "Export the model")
    assert done.intent == "export"
    assert [v["number"] for v in sim.versions] == [1, 2, 3, 4]  # one version per successful change


def test_scenario_b_chair_selected_backrest_color_restore(sim):  # noqa: ANN001
    w = make_worker(sim)
    _, done = say(w, sim, "Create a chair")
    assert done.success and done.metadata["quality"]["passed"], done
    v1 = sim.inventory
    back = sim.objects()["backrest"]

    _, done = say(w, sim, "Make it taller", selected="backrest")
    assert done.success and done.metadata["quality"]["passed"], done
    now = sim.objects()["backrest"]
    assert now["dimensions"][2] == pytest.approx(back["dimensions"][2] * 1.25)
    assert bottom(now) == pytest.approx(bottom(back), abs=1e-3)  # still sits on the seat
    assert changed_ids(v1, sim.inventory) == {"backrest"}
    v2 = sim.inventory

    _, done = say(w, sim, "Change its color to dark blue", selected="backrest")
    assert done.success, done
    objs = sim.objects()
    assert objs["backrest"]["material_ids"] == ["dark_blue_paint"]
    assert objs["seat"]["material_ids"] == ["beech"]  # the seat shared the material and is untouched
    assert changed_ids(v2, sim.inventory) == {"backrest"}
    beech = next(m for m in sim.inventory["materials"] if m["material_id"] == "beech")
    assert beech["base_color"] == "#C8A27A"  # the shared material was not recoloured

    _, done = say(w, sim, "Restore the previous version")
    assert done.success and done.metadata["restored"] == 2
    assert sim.objects()["backrest"]["material_ids"] == ["beech"]
    assert sim.objects()["backrest"]["dimensions"][2] == pytest.approx(back["dimensions"][2] * 1.25)
    assert len(sim.versions) == 3  # restoring creates no version and deletes none


def test_redo_after_undo(sim):  # noqa: ANN001
    w = make_worker(sim)
    say(w, sim, "Create a modern wooden desk")
    say(w, sim, "Change the top to walnut")
    _, done = say(w, sim, "undo")
    assert done.metadata["restored"] == 1
    _, done = say(w, sim, "redo")
    assert done.success and done.intent == "redo" and done.metadata["restored"] == 2
    _, done = say(w, sim, "redo")
    assert not done.success and done.error_code == "nothing_to_redo"


def test_multi_selection_targets_every_selected_object(sim):  # noqa: ANN001
    w = make_worker(sim)
    say(w, sim, "Create a modern wooden desk")
    before = sim.inventory
    _, done = say(w, sim, "Paint them red", selected=["leg_fl", "leg_fr"])
    assert done.success, done
    objs = sim.objects()
    assert objs["leg_fl"]["material_ids"] == objs["leg_fr"]["material_ids"] == ["red_paint"]
    assert objs["leg_bl"]["material_ids"] == ["graphite"]
    assert changed_ids(before, sim.inventory) == {"leg_fl", "leg_fr"}
    # the agents were told about the whole selection
    planning_prompt = next(
        m for role, m in w.runner.workflow.d.providers["planning"].calls[::-1] if role == "planning"
    )[-1].content
    assert "Selected objects: 'Leg Front Left' (id leg_fl), 'Leg Front Right' (id leg_fr)." in planning_prompt

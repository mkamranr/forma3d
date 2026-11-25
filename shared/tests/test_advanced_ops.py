"""Phase 7 operations: schema rules and semantic validation."""

from __future__ import annotations

import pytest

from forma3d_shared.constants.limits import ModelingLimits
from forma3d_shared.schemas.modeling_operation import OperationBatch
from forma3d_shared.schemas.scene_object import MaterialSnapshot, MeshStats, ObjectSnapshot, SceneInventory
from forma3d_shared.validation import BatchValidationError, validate_batch
from forma3d_shared.validation.validator import referenced_image_ids

IMG = "0b6e3d0c-1c1f-4b55-9a51-2c4cbe4a8f10"


def op(oid, type_, target=None, **params):
    d = {"operation_id": oid, "operation_type": type_, "parameters": params}
    if target:
        d["target_object_id"] = target
    return d


def batch(*ops):
    return {"schema_version": "1.0", "operations": list(ops)}


def inventory() -> SceneInventory:
    return SceneInventory(
        objects=[
            ObjectSnapshot(object_id="top", name="Top", mesh=MeshStats(vertices=8)),
            ObjectSnapshot(object_id="leg", name="Leg", parent_id="top", mesh=MeshStats(vertices=64)),
            ObjectSnapshot(object_id="hole", name="Hole", mesh=MeshStats(vertices=64)),
            ObjectSnapshot(object_id="cam", name="Cam", type="CAMERA"),
            ObjectSnapshot(object_id="lamp", name="Lamp", type="LIGHT"),
        ],
        materials=[MaterialSnapshot(material_id="oak", name="Oak")],
    )


def issues(payload, inv=None, limits=None) -> set[str]:
    with pytest.raises(BatchValidationError) as exc:
        validate_batch(payload, inv or inventory(), limits)
    return {i.code for i in exc.value.issues}


# --------------------------------------------------------------------------- schema
def test_new_geometry_ops_parse_and_validate():
    result = validate_batch(
        batch(
            op("a", "create_circle", "disc", name="Disc", radius=0.3, vertices=24),
            op("b", "extrude", "disc", side="top", distance=0.05),
            op("c", "inset", "disc", side="top", thickness=0.02, depth=-0.01),
            op(
                "d",
                "create_mesh",
                "wedge",
                name="Wedge",
                vertices=[[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]],
                faces=[[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]],
            ),
            op("e", "create_curve", "rail", name="Rail", points=[[0, 0, 0], [0.5, 0, 0.2], [1, 0, 0]], thickness=0.02),
            op("f", "subdivide", "wedge", levels=2),
            op("g", "mirror", "rail", axes=["x", "y"]),
            op("h", "array", "disc", count=4, offset=[0.7, 0, 0]),
            op("i", "decimate", "wedge", ratio=0.5),
            op("j", "remesh", "rail", voxel_size=0.01),
            op("k", "apply_transform", "disc"),
            op("l", "set_origin", "disc", mode="bounds_bottom"),
        ),
        SceneInventory(),
    )
    assert result.stats["new_objects"] == 3
    assert result.stats["estimated_vertices"] > 100


@pytest.mark.parametrize(
    "bad",
    [
        op("a", "create_mesh", "m", name="M", vertices=[[0, 0, 0], [1, 0, 0], [0, 1, 0]], faces=[[0, 1, 3]]),
        op("a", "create_mesh", "m", name="M", vertices=[[0, 0, 0], [1, 0, 0], [0, 1, 0]], faces=[[0, 1, 1]]),
        op("a", "extrude", "top", side="top", distance=0),
        op("a", "mirror", "top", axes=["x", "x"]),
        op("a", "set_origin", "top", mode="point"),
        op("a", "assign_texture", None, material_id="oak"),
        op("a", "assign_texture", None, material_id="oak", pattern="wood", image_id=IMG),
        op("a", "assign_texture", None, material_id="oak", pattern="lava"),
        op("a", "assign_texture", None, material_id="oak", image_id="../../etc/passwd"),
        op("a", "add_camera", "c2", name="C", location=[1, 1, 1], look_at=[0, 0, 0], rotation=[0, 0, 0]),
        op("a", "add_light", "l2", name="L", light_type="laser"),
        op("a", "update_light", "lamp"),
        op("a", "separate_objects", "top", new_object_ids=["p", "p"]),
        op("a", "create_curve", "c", name="C", points=[[0, 0, 0]]),
    ],
)
def test_schema_rejections(bad):
    assert issues(batch(bad)) == {"schema"} or "schema" in issues(batch(bad))


def test_material_extras_and_textures():
    result = validate_batch(
        batch(
            op(
                "m1",
                "create_material",
                None,
                material_id="glass",
                name="Glass",
                transmission=1.0,
                roughness=0.05,
                ior=1.5,
            ),
            op("m2", "update_material", None, material_id="oak", emission_color="#FFAA00", emission_strength=2.0),
            op("t1", "assign_texture", None, material_id="oak", pattern="wood", color_a="#C08A50", scale=2.0),
            op("t2", "assign_texture", None, material_id="glass", image_id=IMG),
            op("t3", "remove_texture", None, material_id="glass"),
        ),
        inventory(),
    )
    assert referenced_image_ids(list(result.canonical_operations)) == [IMG]
    assert issues(batch(op("t", "assign_texture", None, material_id="nope", pattern="wood"))) == {"unknown_material"}


# --------------------------------------------------------------------------- semantics
def test_mesh_only_ops_reject_cameras_and_lights():
    assert issues(batch(op("a", "bevel", "cam"))) == {"not_a_mesh"}
    assert issues(batch(op("a", "set_dimensions", "lamp", dimensions=[1, 1, 1]))) == {"not_a_mesh"}
    assert issues(batch(op("a", "assign_material", "lamp", material_id="oak"))) == {"not_a_mesh"}
    assert issues(batch(op("a", "update_camera", "lamp", lens=35))) == {"not_a_camera"}
    assert issues(batch(op("a", "update_light", "cam", energy=10))) == {"not_a_light"}


def test_booleans_consume_the_operand():
    result = validate_batch(batch(op("b", "boolean_difference", "top", operand_id="hole")), inventory())
    assert result.stats["scene_objects"] == 4  # the cutter is deleted
    # the operand is gone afterwards, so a later reference fails
    assert issues(
        batch(op("b", "boolean_difference", "top", operand_id="hole"), op("m", "move_object", "hole", offset=[1, 0, 0]))
    ) == {"unknown_object"}
    kept = validate_batch(batch(op("b", "boolean_union", "top", operand_id="hole", keep_operand=True)), inventory())
    assert kept.stats["scene_objects"] == 5


def test_boolean_rules():
    assert issues(batch(op("b", "boolean_union", "top", operand_id="top"))) == {"invalid_operand"}
    assert issues(batch(op("b", "boolean_union", "top", operand_id="cam"))) == {"not_a_mesh"}
    assert issues(batch(op("b", "boolean_difference", "top", operand_id="ghost"))) == {"unknown_object"}
    # deleting a parent through a boolean would orphan its children
    assert issues(batch(op("b", "boolean_difference", "hole", operand_id="top"))) == {"has_children"}
    # an operand that is the target's own parent cannot be consumed
    assert issues(batch(op("b", "boolean_difference", "leg", operand_id="top"))) == {"invalid_operand"}


def test_join_and_separate():
    joined = validate_batch(batch(op("j", "join_objects", "hole", object_ids=["leg"])), inventory())
    assert joined.stats["scene_objects"] == 4
    assert issues(batch(op("j", "join_objects", "leg", object_ids=["top"]))) == {"invalid_operand"}
    assert issues(batch(op("j", "join_objects", "top", object_ids=["cam"]))) == {"not_a_mesh"}
    sep = validate_batch(batch(op("s", "separate_objects", "hole", new_object_ids=["hole_b", "hole_c"])), inventory())
    assert sep.stats["new_objects"] == 2
    assert issues(batch(op("s", "separate_objects", "hole", new_object_ids=["leg"]))) == {"object_exists"}


def test_cameras_lights_world():
    result = validate_batch(
        batch(
            op("c", "add_camera", "cam2", name="Front Camera", location=[0, -4, 1.5], look_at=[0, 0, 0.7], lens=35),
            op(
                "l",
                "add_light",
                "key",
                name="Key Light",
                light_type="area",
                location=[2, -2, 3],
                look_at=[0, 0, 0],
                energy=800,
                radius=1.0,
            ),
            op("u", "update_light", "key", color="#FFE8C0"),
            op("u2", "update_camera", "cam2", lens=50, set_active=True),
            op("p", "parent_object", "key", parent_id="cam2"),
            op("w", "set_world_background", None, color="#101418", strength=0.5),
        ),
        inventory(),
    )
    assert result.stats["new_objects"] == 2
    assert issues(batch(op("c", "add_camera", "cam", name="C", location=[0, 0, 1]))) == {"object_exists"}


def test_modifier_limits():
    limits = ModelingLimits(max_subdivision_levels=2, max_array_count=10, max_vertices_per_object=5000)
    assert issues(batch(op("s", "subdivide", "top", levels=3)), limits=limits) >= {"limit_subdivision"}
    assert issues(batch(op("a", "array", "top", count=11)), limits=limits) == {"limit_array"}
    # 64 vertices x 4^2 x 10 copies is over the per-object vertex budget
    assert issues(batch(op("s", "subdivide", "hole", levels=2), op("a", "array", "hole", count=10)), limits=limits) == {
        "limit_vertices"
    }


def test_dsl_strings_remain_data_only():
    payload = batch(op("a", "add_light", "l", name="__import__('os')"))
    assert "schema" in issues(payload)
    assert OperationBatch.model_validate(batch(op("a", "add_light", "l", name="Lamp")))

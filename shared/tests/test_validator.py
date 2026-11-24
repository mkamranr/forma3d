from __future__ import annotations

import pytest

from forma3d_shared.constants.limits import ModelingLimits
from forma3d_shared.examples import black_metal_legs_batch, desk_batch
from forma3d_shared.schemas.scene_object import (
    CollectionSnapshot,
    MaterialSnapshot,
    MeshStats,
    ObjectSnapshot,
    SceneInventory,
)
from forma3d_shared.validation import BatchValidationError, validate_batch


def desk_inventory() -> SceneInventory:
    objs = [ObjectSnapshot(object_id="desk_top", name="Desk Top", mesh=MeshStats(vertices=8))]
    for leg in ("leg_fl", "leg_fr", "leg_bl", "leg_br"):
        objs.append(ObjectSnapshot(object_id=leg, name=leg, parent_id="desk_top", mesh=MeshStats(vertices=8)))
    return SceneInventory(
        objects=objs,
        materials=[
            MaterialSnapshot(material_id="oak", name="Oak"),
            MaterialSnapshot(material_id="graphite", name="Graphite"),
        ],
        collections=[CollectionSnapshot(collection_id="desk", name="Desk")],
    )


def codes(exc: pytest.ExceptionInfo[BatchValidationError]) -> set[str]:
    return {i.code for i in exc.value.issues}


def batch(*ops):
    return {"schema_version": "1.0", "operations": list(ops)}


def cube(oid, **params):
    return {
        "operation_id": f"op_{oid}",
        "operation_type": "create_cube",
        "target_object_id": oid,
        "parameters": {"name": oid.title(), **params},
    }


def test_desk_on_empty_scene_is_valid():
    result = validate_batch(desk_batch())
    assert result.stats["new_objects"] == 5
    assert len(result.sha256) == 64
    assert result.canonical_operations[0]["operation_type"] == "create_collection"


def test_hash_is_deterministic():
    assert validate_batch(desk_batch()).sha256 == validate_batch(desk_batch()).sha256


def test_modification_against_existing_scene():
    result = validate_batch(black_metal_legs_batch(), desk_inventory())
    assert result.stats["new_objects"] == 0


def test_modification_against_empty_scene_fails_with_all_issues():
    with pytest.raises(BatchValidationError) as exc:
        validate_batch(black_metal_legs_batch())
    assert codes(exc) == {"unknown_object"}
    assert len(exc.value.issues) == 4  # collected, not fail-fast


def test_recreating_existing_object_is_rejected():
    with pytest.raises(BatchValidationError) as exc:
        validate_batch(desk_batch(), desk_inventory())
    assert {"object_exists", "material_exists", "collection_exists"} <= codes(exc)


def test_unknown_references():
    with pytest.raises(BatchValidationError) as exc:
        validate_batch(batch(cube("a", parent_id="ghost", material_id="nope", collection_id="none")))
    assert codes(exc) == {"unknown_object", "unknown_material", "unknown_collection"}


def test_dependencies_must_point_backwards():
    a = cube("a")
    a["dependencies"] = ["op_b"]
    with pytest.raises(BatchValidationError) as exc:
        validate_batch(batch(a, cube("b")))
    assert codes(exc) == {"bad_dependency"}


def test_duplicate_operation_ids():
    b = cube("b")
    b["operation_id"] = "op_a"
    with pytest.raises(BatchValidationError) as exc:
        validate_batch(batch(cube("a"), b))
    assert "duplicate_operation_id" in codes(exc)


def test_hierarchy_cycle_rejected():
    op = {
        "operation_id": "p",
        "operation_type": "parent_object",
        "target_object_id": "desk_top",
        "parameters": {"parent_id": "leg_fl"},
    }
    with pytest.raises(BatchValidationError) as exc:
        validate_batch(batch(op), desk_inventory())
    assert codes(exc) == {"hierarchy_cycle"}


def test_delete_with_children_requires_recursive():
    op = {"operation_id": "d", "operation_type": "delete_object", "target_object_id": "desk_top"}
    with pytest.raises(BatchValidationError) as exc:
        validate_batch(batch(op), desk_inventory())
    assert codes(exc) == {"has_children"}

    op["parameters"] = {"recursive": True}
    result = validate_batch(batch(op), desk_inventory())
    assert result.stats["scene_objects"] == 0


def test_use_after_delete_is_rejected():
    delete = {"operation_id": "d", "operation_type": "delete_object", "target_object_id": "leg_fl"}
    move = {
        "operation_id": "m",
        "operation_type": "move_object",
        "target_object_id": "leg_fl",
        "parameters": {"offset": [0, 0, 1]},
    }
    with pytest.raises(BatchValidationError) as exc:
        validate_batch(batch(delete, move), desk_inventory())
    assert codes(exc) == {"unknown_object"}


def test_create_then_modify_in_same_batch():
    mat = {
        "operation_id": "mat",
        "operation_type": "create_material",
        "parameters": {"material_id": "red", "name": "Red"},
    }
    assign = {
        "operation_id": "as",
        "operation_type": "assign_material",
        "target_object_id": "a",
        "parameters": {"material_id": "red"},
    }
    dup = {
        "operation_id": "dup",
        "operation_type": "duplicate_object",
        "target_object_id": "a",
        "parameters": {"new_object_id": "a_copy", "name": "A copy", "offset": [1, 0, 0]},
    }
    result = validate_batch(batch(cube("a"), mat, assign, dup))
    assert result.stats["new_objects"] == 2


def test_limits_are_enforced():
    limits = ModelingLimits(max_operations_per_batch=3, max_new_objects_per_batch=2, max_scene_objects=2)
    with pytest.raises(BatchValidationError) as exc:
        validate_batch(batch(cube("a"), cube("b"), cube("c"), cube("d")), limits=limits)
    assert {"limit_operations", "limit_new_objects", "limit_scene_objects"} <= codes(exc)


def test_vertex_limit_includes_bevel_growth():
    sphere = {
        "operation_id": "s",
        "operation_type": "create_sphere",
        "target_object_id": "ball",
        "parameters": {"name": "Ball", "segments": 128, "rings": 64},
    }
    bevel = {"operation_id": "b", "operation_type": "bevel", "target_object_id": "ball", "parameters": {"segments": 10}}
    with pytest.raises(BatchValidationError) as exc:
        validate_batch(batch(sphere, bevel), limits=ModelingLimits(max_vertices_per_object=50_000))
    assert "limit_vertices" in codes(exc)


def test_bevel_segment_limit():
    bevel = {
        "operation_id": "b",
        "operation_type": "bevel",
        "target_object_id": "desk_top",
        "parameters": {"segments": 20},
    }
    with pytest.raises(BatchValidationError) as exc:
        validate_batch(batch(bevel), desk_inventory(), ModelingLimits(max_bevel_segments=10))
    assert codes(exc) == {"limit_bevel_segments"}


def test_structural_errors_carry_operation_id_and_location():
    bad = cube("a")
    bad["parameters"]["dimensions"] = [-1, 1, 1]
    with pytest.raises(BatchValidationError) as exc:
        validate_batch(batch(bad))
    issue = exc.value.issues[0]
    assert issue.code == "schema" and issue.operation_id == "op_a"
    assert issue.location.startswith("operations.0")


def test_unknown_operation_type_code():
    with pytest.raises(BatchValidationError) as exc:
        validate_batch(
            batch({"operation_id": "x", "operation_type": "exec_python", "parameters": {"code": "print(1)"}})
        )
    assert codes(exc) == {"unknown_operation"}


def test_empty_batch_rejected():
    with pytest.raises(BatchValidationError):
        validate_batch(batch())

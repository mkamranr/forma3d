from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from forma3d_shared.examples import desk_batch
from forma3d_shared.schemas.modeling_operation import (
    ALLOWED_OPERATIONS,
    OPERATION_MODELS,
    OperationBatch,
    operation_adapter,
)


def op(**kw):
    return operation_adapter.validate_python(kw)


def test_allowlist_matches_models():
    assert len(ALLOWED_OPERATIONS) == len(OPERATION_MODELS) == 27
    assert {"create_cube", "set_dimensions", "assign_material", "bevel"} <= ALLOWED_OPERATIONS
    assert "clear_scene" not in ALLOWED_OPERATIONS


def test_spec_example_operation_parses():
    o = op(
        operation_id="op_001",
        operation_type="create_cube",
        target_object_id="desk_top",
        parameters={"name": "Desk Top", "dimensions": [2.0, 1.0, 0.12], "location": [0.0, 0.0, 1.0]},
        dependencies=[],
        metadata={"component": "tabletop"},
    )
    assert o.parameters.dimensions == (2.0, 1.0, 0.12)


def test_desk_example_is_valid():
    batch = OperationBatch.model_validate(desk_batch())
    assert len(batch.operations) == 9


@pytest.mark.parametrize(
    "mutation",
    [
        lambda o: o.update(operation_type="run_python"),  # not allowlisted
        lambda o: o.update(code="import os"),  # unknown top-level field
        lambda o: o["parameters"].update(script="bpy.ops.wm.quit_blender()"),  # unknown parameter
        lambda o: o["parameters"].update(dimensions=[0, 1, 1]),  # zero dimension
        lambda o: o["parameters"].update(dimensions=[1e9, 1, 1]),  # out of range
        lambda o: o["parameters"].update(dimensions=[float("nan"), 1, 1]),  # NaN
        lambda o: o["parameters"].update(location=[0, 0]),  # wrong arity
        lambda o: o["parameters"].update(name="__import__('os')"),  # code-like name
        lambda o: o["parameters"].update(name="../../etc/passwd"),  # path-like name
        lambda o: o.update(target_object_id="Desk Top"),  # invalid id
        lambda o: o.update(target_object_id=None),  # missing target
        lambda o: o["metadata"].update(note="`rm -rf /`"),  # shell in metadata
    ],
)
def test_rejects_malicious_or_invalid(mutation):
    raw = copy.deepcopy(
        {
            "operation_id": "op_1",
            "operation_type": "create_cube",
            "target_object_id": "box_a",
            "parameters": {"name": "Box", "dimensions": [1, 1, 1]},
            "metadata": {},
        }
    )
    mutation(raw)
    with pytest.raises(ValidationError):
        op(**raw)


def test_material_ops_must_not_have_target():
    with pytest.raises(ValidationError):
        op(
            operation_id="m",
            operation_type="create_material",
            target_object_id="x",
            parameters={"material_id": "oak", "name": "Oak"},
        )


@pytest.mark.parametrize(
    "params",
    [{}, {"location": [0, 0, 1], "offset": [0, 0, 1]}],
)
def test_move_requires_exactly_one_mode(params):
    with pytest.raises(ValidationError):
        op(operation_id="m", operation_type="move_object", target_object_id="a", parameters=params)


def test_update_material_requires_a_change():
    with pytest.raises(ValidationError):
        op(operation_id="m", operation_type="update_material", parameters={"material_id": "oak"})


def test_torus_rejects_self_intersection():
    with pytest.raises(ValidationError):
        op(
            operation_id="t",
            operation_type="create_torus",
            target_object_id="ring",
            parameters={"name": "Ring", "major_radius": 0.2, "minor_radius": 0.3},
        )


def test_schema_version_is_checked():
    raw = desk_batch()
    raw["schema_version"] = "2.0"
    with pytest.raises(ValidationError):
        OperationBatch.model_validate(raw)


def test_unicode_names_allowed():
    o = op(
        operation_id="x",
        operation_type="rename_object",
        target_object_id="a",
        parameters={"name": "Schreibtisch Ä · 2"},
    )
    assert o.parameters.name.startswith("Schreibtisch")

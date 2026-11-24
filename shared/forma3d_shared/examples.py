"""Reference DSL batches (spec §23 prototype scenario). Used by tests, the Blender integration
suite and, later, the fake LLM provider. Units: meters, Z up."""

from __future__ import annotations

from typing import Any

TOP_Z = 0.74  # tabletop centre height
LEG_H = 0.72


def desk_batch() -> dict[str, Any]:
    """A simple modern desk: tabletop + 4 legs, oak and graphite materials."""
    legs = []
    for oid, name, x, y in [
        ("leg_fl", "Leg Front Left", -0.74, -0.34),
        ("leg_fr", "Leg Front Right", 0.74, -0.34),
        ("leg_bl", "Leg Back Left", -0.74, 0.34),
        ("leg_br", "Leg Back Right", 0.74, 0.34),
    ]:
        legs.append(
            {
                "operation_id": f"op_{oid}",
                "operation_type": "create_cube",
                "target_object_id": oid,
                "dependencies": ["op_mat_frame", "op_desk_top"],
                "parameters": {
                    "name": name,
                    "dimensions": [0.05, 0.05, LEG_H],
                    "location": [x, y, LEG_H / 2],
                    "parent_id": "desk_top",
                    "material_id": "graphite",
                    "collection_id": "desk",
                },
                "metadata": {"component": "leg"},
            }
        )
    return {
        "schema_version": "1.0",
        "summary": "Create a modern wooden desk with four legs",
        "operations": [
            {
                "operation_id": "op_col",
                "operation_type": "create_collection",
                "parameters": {"collection_id": "desk", "name": "Desk"},
            },
            {
                "operation_id": "op_mat_top",
                "operation_type": "create_material",
                "parameters": {"material_id": "oak", "name": "Oak", "base_color": "#C99563", "roughness": 0.6},
            },
            {
                "operation_id": "op_mat_frame",
                "operation_type": "create_material",
                "parameters": {
                    "material_id": "graphite",
                    "name": "Graphite",
                    "base_color": "#424953",
                    "metallic": 0.6,
                    "roughness": 0.4,
                },
            },
            {
                "operation_id": "op_desk_top",
                "operation_type": "create_cube",
                "target_object_id": "desk_top",
                "dependencies": ["op_col", "op_mat_top"],
                "parameters": {
                    "name": "Desk Top",
                    "dimensions": [1.6, 0.8, 0.04],
                    "location": [0.0, 0.0, TOP_Z],
                    "material_id": "oak",
                    "collection_id": "desk",
                },
                "metadata": {"component": "tabletop"},
            },
            {
                "operation_id": "op_bevel_top",
                "operation_type": "bevel",
                "target_object_id": "desk_top",
                "dependencies": ["op_desk_top"],
                "parameters": {"width": 0.006, "segments": 3},
            },
            *legs,
        ],
    }


def black_metal_legs_batch() -> dict[str, Any]:
    """Follow-up modification: 'make the legs black metal' — touches only the legs."""
    ops: list[dict[str, Any]] = [
        {
            "operation_id": "op_mat_black_metal",
            "operation_type": "create_material",
            "parameters": {
                "material_id": "black_metal",
                "name": "Black Metal",
                "base_color": "#111111",
                "metallic": 1.0,
                "roughness": 0.35,
            },
        }
    ]
    for leg in ("leg_fl", "leg_fr", "leg_bl", "leg_br"):
        ops.append(
            {
                "operation_id": f"op_assign_{leg}",
                "operation_type": "assign_material",
                "target_object_id": leg,
                "dependencies": ["op_mat_black_metal"],
                "parameters": {"material_id": "black_metal"},
            }
        )
    return {"schema_version": "1.0", "summary": "Make the legs black metal", "operations": ops}

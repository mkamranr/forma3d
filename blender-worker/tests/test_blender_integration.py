"""Spec §17.2 Blender integration tests, run against a real Blender binary (BLENDER_BIN).

1 cube · 2 sphere · 3 cylinder · 4 multiple objects · 5 transforms · 6 bevel · 7 create material
8 assign material · 9 duplicate · 10 delete · 11 save & reload · 12 custom metadata preserved
13 export GLB · 14 inspect exported GLB · 15 thumbnail · 16 invalid op leaves committed version intact
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from forma3d_shared.constants.limits import ModelingLimits
from forma3d_shared.examples import black_metal_legs_batch, desk_batch
from forma3d_shared.schemas.job import BlenderJobResult, SourceVersion

from app.artifacts import glb_node_object_ids, sha256_file
from app.config import WorkerConfig
from app.job_runner import execute_job
from tests.conftest import BLENDER_BIN, canonical, make_spec, requires_blender

pytestmark = [pytest.mark.blender, requires_blender]


def op(oid, type_, target=None, **params):
    d = {"operation_id": oid, "operation_type": type_, "parameters": params}
    if target:
        d["target_object_id"] = target
    return d


PRIMITIVES = {
    "schema_version": "1.0",
    "operations": [
        op("c1", "create_cube", "box", name="Box", dimensions=[1.0, 2.0, 0.5], location=[0, 0, 0.25]),
        op("c2", "create_sphere", "ball", name="Ball", radius=0.3, segments=16, rings=8, location=[2, 0, 0.3]),
        op("c3", "create_cylinder", "pipe", name="Pipe", radius=0.1, depth=1.0, vertices=12, location=[-2, 0, 0.5]),
        op("c4", "create_cone", "cone", name="Cone", radius_bottom=0.3, depth=0.6, location=[0, 2, 0.3]),
        op("c5", "create_torus", "ring", name="Ring", major_radius=0.4, minor_radius=0.05, location=[0, -2, 0.05]),
        op("c6", "create_plane", "floor", name="Floor", size=[5, 5]),
        op("t1", "move_object", "ball", offset=[0, 0, 0.5]),
        op("t2", "rotate_object", "pipe", rotation=[90, 0, 0]),
        op("t3", "scale_object", "cone", factor=2.0),
        op("t4", "set_dimensions", "box", dimensions=[1.5, 2.0, 0.5]),
        op("b1", "bevel", "box", width=0.05, segments=2),
        op("m1", "create_material", None, material_id="red", name="Red", base_color="#CC2222", roughness=0.3),
        op("m2", "assign_material", "box", material_id="red"),
        op("d1", "duplicate_object", "box", new_object_id="box_copy", name="Box Copy", offset=[0, 0, 1]),
        op("x1", "delete_object", "ring"),
        op("p1", "parent_object", "ball", parent_id="box"),
        op("v1", "set_visibility", "floor", visible=False),
    ],
}


@pytest.fixture(scope="module")
def module_cfg(tmp_path_factory) -> WorkerConfig:
    root = tmp_path_factory.mktemp("blender")
    return WorkerConfig(
        data_root=root / "data",
        work_root=root / "work",
        blender_bin=BLENDER_BIN or "",
        worker_id="it",
        limits=ModelingLimits(job_timeout_seconds=240),
        liveness_file=root / "alive",
    )


def staging_path(cfg: WorkerConfig, result: BlenderJobResult) -> Path:
    return next(cfg.data_root.glob(f"projects/*/*/staging/{result.job_id}"))


def promote(cfg: WorkerConfig, result: BlenderJobResult, version: int) -> SourceVersion:
    """Mimic the backend commit: staging -> versions/vNNNN."""
    staging = staging_path(cfg, result)
    target = staging.parent.parent / "versions" / f"v{version:04d}"
    target.parent.mkdir(parents=True, exist_ok=True)
    staging.rename(target)
    rel = target.relative_to(cfg.data_root).as_posix() + "/scene.blend"
    return SourceVersion(
        version_id=uuid.uuid4(),
        version_number=version,
        blend_path=rel,
        blend_sha256=sha256_file(target / "scene.blend"),
    )


@pytest.fixture(scope="module")
def primitives_run(module_cfg):
    events: list[str] = []
    result = execute_job(
        make_spec(canonical(PRIMITIVES), thumbnail=True), module_cfg, emit=lambda s, m, d: events.append(s)
    )
    assert result.success, (result.error_code, result.error_message, result.log_tail)
    return result, events


def test_primitives_transforms_materials_duplicate_delete(primitives_run):  # cases 1-10
    result, events = primitives_run
    objs = result.inventory.object_map()
    assert set(objs) == {"box", "ball", "pipe", "cone", "floor", "box_copy"}  # ring deleted (10)
    assert objs["box"].dimensions == pytest.approx((1.5, 2.0, 0.5), abs=1e-4)  # set_dimensions (5)
    assert objs["box"].scale == pytest.approx((1, 1, 1))  # baked into mesh data
    assert objs["cone"].scale == pytest.approx((2, 2, 2))  # scale_object (5)
    assert objs["pipe"].rotation == pytest.approx((90, 0, 0))  # rotate (5)
    assert objs["ball"].location == pytest.approx((2, 0, 0.8))  # move offset, world-space kept after parenting
    assert objs["ball"].parent_id == "box"
    assert [m.type for m in objs["box"].modifiers] == ["BEVEL"]  # bevel (6)
    assert objs["box"].mesh.vertices > 8  # evaluated with bevel
    assert objs["ball"].mesh.vertices == 16 * 7 + 2  # sphere (2)
    assert objs["pipe"].mesh.vertices == 24  # cylinder (3)
    assert {m.material_id for m in result.inventory.materials} == {"red"}  # create material (7)
    assert objs["box"].material_ids == ["red"]  # assign (8)
    assert objs["box_copy"].material_ids == ["red"]  # duplicate keeps material (9)
    assert objs["box_copy"].location == pytest.approx((0, 0, 1.25))
    assert objs["floor"].visible is False
    assert "executing" in events and "rendering" in events


def test_artifacts_glb_and_thumbnail(primitives_run, module_cfg):  # cases 13-15
    result, _ = primitives_run
    kinds = {a.kind for a in result.artifacts}
    assert kinds == {"blend", "glb", "thumbnail", "inventory", "operations"}
    staging = staging_path(module_cfg, result)
    for a in result.artifacts:
        assert sha256_file(staging / a.filename) == a.sha256
    assert glb_node_object_ids(staging / "scene.glb") == set(result.inventory.object_map())
    assert (staging / "thumbnail.png").read_bytes()[:4] == b"\x89PNG"


def test_desk_modify_reload_metadata_and_failure_isolation(module_cfg):  # cases 11, 12, 16
    v1 = execute_job(make_spec(canonical(desk_batch()), version=1), module_cfg)
    assert v1.success, v1.error_message
    source_v1 = promote(module_cfg, v1, 1)
    inventory_v1 = v1.inventory

    # 11/12: reload v1 and modify only the legs.
    v2 = execute_job(
        make_spec(canonical(black_metal_legs_batch(), inventory_v1), version=2, source=source_v1), module_cfg
    )
    assert v2.success, (v2.error_code, v2.error_message, v2.log_tail)
    o1, o2 = inventory_v1.object_map(), v2.inventory.object_map()
    assert set(o1) == set(o2)
    for leg in ("leg_fl", "leg_fr", "leg_bl", "leg_br"):
        assert o2[leg].material_ids == ["black_metal"]
        assert o2[leg].created_version == 1 and o2[leg].last_modified_version == 2
        assert o2[leg].location == o1[leg].location and o2[leg].parent_id == "desk_top"
    # Unaffected object is byte-for-byte identical in the inventory, metadata included.
    assert o2["desk_top"] == o1["desk_top"]
    assert o2["desk_top"].last_modified_version == 1

    # 16: an operation failing mid-batch must not touch the committed version.
    committed = module_cfg.data_root / source_v1.blend_path
    before = sha256_file(committed)
    bad = {
        "schema_version": "1.0",
        "operations": [
            op("ok", "move_object", "desk_top", offset=[0, 0, 0.1]),
            op("plane", "create_plane", "mat", name="Mat", size=[1, 1]),
            op("boom", "set_dimensions", "mat", dimensions=[1, 1, 0.2]),  # flat along Z -> runtime error
        ],
    }
    v3 = execute_job(make_spec(canonical(bad, inventory_v1), version=2, source=source_v1), module_cfg)
    assert not v3.success
    assert v3.error_code == "flat_axis" and v3.failed_operation_id == "boom"
    assert sha256_file(committed) == before
    assert not list(module_cfg.data_root.glob(f"projects/*/*/staging/{v3.job_id}*"))
    assert not (module_cfg.work_root / str(v3.job_id)).exists()


def test_cancellation_terminates_blender(module_cfg):
    result = execute_job(make_spec(canonical(desk_batch())), module_cfg, is_cancelled=lambda: True)
    assert not result.success and result.error_code == "cancelled"
    assert not list(module_cfg.data_root.glob(f"projects/*/*/staging/{result.job_id}*"))

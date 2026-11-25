"""Phase 7 Blender integration: advanced geometry, materials/textures, cameras/lights, render and
export jobs — run against a real Blender binary (BLENDER_BIN)."""

from __future__ import annotations

import json
import struct
import uuid
import zipfile
import zlib
from pathlib import Path

import pytest
from forma3d_shared.constants.limits import ModelingLimits
from forma3d_shared.schemas.job import BlenderJobSpec, ExportSettings, RenderSettings, SourceVersion, TextureFile

from app.artifacts import glb_node_object_ids, sha256_file
from app.config import WorkerConfig
from app.job_runner import batch_digest, execute_job
from tests.conftest import BLENDER_BIN, PROJECT, USER, canonical, make_spec, requires_blender

pytestmark = [pytest.mark.blender, requires_blender]


def op(oid, type_, target=None, **params):
    d = {"operation_id": oid, "operation_type": type_, "parameters": params}
    if target:
        d["target_object_id"] = target
    return d


@pytest.fixture(scope="module")
def cfg(tmp_path_factory) -> WorkerConfig:
    root = tmp_path_factory.mktemp("blender7")
    return WorkerConfig(
        data_root=root / "data",
        work_root=root / "work",
        blender_bin=BLENDER_BIN or "",
        worker_id="it7",
        limits=ModelingLimits(job_timeout_seconds=300),
        liveness_file=root / "alive",
    )


def run(cfg, batch, inventory=None, **kw):
    result = execute_job(make_spec(canonical(batch, inventory), **kw), cfg)
    assert result.success, (
        result.error_code,
        result.error_message,
        result.failed_operation_id,
        result.log_tail[-1500:],
    )
    return result


def promote(cfg: WorkerConfig, result, version: int) -> SourceVersion:
    staging = next(cfg.data_root.glob(f"projects/*/*/staging/{result.job_id}"))
    target = staging.parent.parent / "versions" / f"v{version:04d}-{result.job_id.hex[:6]}"
    target.parent.mkdir(parents=True, exist_ok=True)
    staging.rename(target)
    return SourceVersion(
        version_id=uuid.uuid4(),
        version_number=version,
        blend_path=target.relative_to(cfg.data_root).as_posix() + "/scene.blend",
        blend_sha256=sha256_file(target / "scene.blend"),
    )


def glb_json(path: Path) -> dict:
    data = path.read_bytes()
    n = struct.unpack("<I", data[12:16])[0]
    return json.loads(data[20 : 20 + n])


def B(*ops):
    return {"schema_version": "1.0", "operations": list(ops)}


# --------------------------------------------------------------------------- geometry
GEOMETRY = B(
    op("c1", "create_circle", "disc", name="Disc", radius=0.5, vertices=32, location=[0, 0, 0]),
    op("e1", "extrude", "disc", side="top", distance=0.1),
    op("i1", "inset", "disc", side="top", thickness=0.05, depth=-0.02),
    op(
        "m1",
        "create_mesh",
        "wedge",
        name="Wedge",
        location=[2, 0, 0],
        vertices=[[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]],
        faces=[[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]],
    ),
    op(
        "k1",
        "create_curve",
        "rail",
        name="Rail",
        location=[0, 2, 0],
        points=[[0, 0, 0], [0.5, 0, 0.3], [1, 0, 0]],
        thickness=0.04,
    ),
    op("b0", "create_cube", "slab", name="Slab", dimensions=[1, 1, 0.2], location=[0, -2, 0.1]),
    op("b1", "create_cylinder", "drill", name="Drill", radius=0.15, depth=1.0, location=[0, -2, 0.1]),
    op("b2", "boolean_difference", "slab", operand_id="drill"),
    op("u0", "create_cube", "lump_a", name="Lump A", dimensions=[0.5, 0.5, 0.5], location=[3, -2, 0.25]),
    op("u1", "create_cube", "lump_b", name="Lump B", dimensions=[0.5, 0.5, 0.5], location=[3.3, -2, 0.25]),
    op("u2", "boolean_union", "lump_a", operand_id="lump_b"),
    op("s1", "create_cube", "blob", name="Blob", dimensions=[0.4, 0.4, 0.4], location=[-2, 0, 0.2]),
    op("s2", "subdivide", "blob", levels=2),
    op("s3", "decimate", "blob", ratio=0.5),
    op("a1", "create_cube", "slat", name="Slat", dimensions=[0.05, 0.6, 0.02], location=[-3, 2, 0.5]),
    op("a2", "array", "slat", count=5, offset=[0.1, 0, 0]),
    op("r1", "create_cube", "half", name="Half", dimensions=[0.3, 0.3, 0.3], location=[-3, -2, 0.15]),
    op("r2", "mirror", "half", axes=["x"], mirror_object_id="blob"),
    op("o1", "create_cube", "crate", name="Crate", dimensions=[0.4, 0.4, 0.4], location=[4, 2, 1.0]),
    op("o2", "set_origin", "crate", mode="bounds_bottom"),
    op("o3", "rotate_object", "crate", rotation=[0, 0, 30]),
    op("o4", "apply_transform", "crate"),
    op("j0", "create_cube", "part_a", name="Part A", dimensions=[0.2, 0.2, 0.2], location=[5, 0, 0.1]),
    op("j1", "create_cube", "part_b", name="Part B", dimensions=[0.2, 0.2, 0.2], location=[5.5, 0, 0.1]),
    op("j2", "create_cube", "part_c", name="Part C", dimensions=[0.2, 0.2, 0.2], location=[6.0, 0, 0.1]),
    op("j3", "join_objects", "part_a", object_ids=["part_b", "part_c"]),
)


@pytest.fixture(scope="module")
def geometry(cfg):
    return run(cfg, GEOMETRY, thumbnail=True)


def test_geometry_ops(geometry):
    objs = geometry.inventory.object_map()
    assert "drill" not in objs and "lump_b" not in objs and "part_b" not in objs  # operands consumed
    disc = objs["disc"]
    assert disc.dimensions == pytest.approx((1.0, 1.0, 0.1), abs=2e-3)  # extruded circle -> solid
    assert disc.mesh.vertices > 64  # inset adds a ring
    assert objs["wedge"].mesh.faces == 4
    assert objs["rail"].mesh.vertices > 50 and objs["rail"].dimensions[2] > 0.25  # tube along a curve
    slab = objs["slab"]
    assert slab.dimensions == pytest.approx((1, 1, 0.2), abs=1e-3) and slab.mesh.vertices > 8  # hole drilled
    assert objs["lump_a"].dimensions[0] == pytest.approx(0.8, abs=1e-3)  # union spans both cubes
    assert [m.type for m in objs["blob"].modifiers] == ["SUBSURF", "DECIMATE"]
    assert objs["slat"].dimensions[0] == pytest.approx(0.45, abs=1e-3)  # 5 copies, 0.1 apart
    assert objs["slat"].modifiers[0].params["count"] == 5
    # half mirrored across the blob's origin (x=-2): spans -3.15 .. -0.85
    assert objs["half"].dimensions[0] == pytest.approx(2.3, abs=1e-3)
    crate = objs["crate"]
    assert crate.location == pytest.approx((4, 2, 0.8), abs=1e-4)  # origin at the bottom
    assert crate.rotation == pytest.approx((0, 0, 0))  # rotation baked into the mesh
    assert crate.dimensions[0] == pytest.approx(0.4 * (0.866 + 0.5), abs=1e-3)
    assert objs["part_a"].mesh.vertices == 24 and objs["part_a"].dimensions[0] == pytest.approx(1.2, abs=1e-4)


def test_geometry_glb_complete(geometry, cfg):
    staging = next(cfg.data_root.glob(f"projects/*/*/staging/{geometry.job_id}"))
    assert glb_node_object_ids(staging / "scene.glb") == set(geometry.inventory.object_map())


def test_separate_loose_parts(cfg, geometry):
    source = promote(cfg, geometry, 1)
    sep = run(
        cfg,
        B(op("s", "separate_objects", "part_a", new_object_ids=["part_mid", "part_right"])),
        geometry.inventory,
        version=2,
        source=source,
    )
    objs = sep.inventory.object_map()
    assert objs["part_mid"].location[0] == pytest.approx(5.0)  # shares part_a's origin
    widths = sorted(round(objs[k].dimensions[0], 3) for k in ("part_a", "part_mid", "part_right"))
    assert widths == [0.2, 0.2, 0.2]
    assert objs["part_right"].created_version == 2
    bad = execute_job(
        make_spec(
            canonical(B(op("s", "separate_objects", "part_a", new_object_ids=["only_one"])), geometry.inventory),
            version=2,
            source=source,
        ),
        cfg,
    )
    assert not bad.success and bad.error_code == "separate_mismatch" and "3 parts" in bad.error_message


# --------------------------------------------------------------------------- materials, textures, lights
def tiny_png(path: Path) -> None:
    w = h = 4
    raw = b"".join(b"\x00" + bytes([200, 40, 40] * w) for _ in range(h))

    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


LOOK = B(
    op("m1", "create_material", None, material_id="walnut", name="Walnut", base_color="#6B4423", roughness=0.6),
    op("m2", "assign_texture", None, material_id="walnut", pattern="wood", scale=2.0, rotation=90),
    op(
        "m3",
        "create_material",
        None,
        material_id="glass",
        name="Glass",
        base_color="#E8F4FF",
        transmission=1.0,
        roughness=0.05,
        ior=1.5,
    ),
    op("m4", "create_material", None, material_id="neon", name="Neon", base_color="#FF2BD6", emission_strength=4.0),
    op("m5", "create_material", None, material_id="poster", name="Poster"),
    op(
        "c1",
        "create_cube",
        "table",
        name="Table",
        dimensions=[1.2, 0.6, 0.05],
        location=[0, 0, 0.75],
        material_id="walnut",
    ),
    op("c2", "create_sphere", "vase", name="Vase", radius=0.1, location=[0.3, 0, 0.88], material_id="glass"),
    op(
        "c3",
        "create_torus",
        "ring",
        name="Ring",
        major_radius=0.2,
        minor_radius=0.02,
        location=[-0.3, 0, 0.8],
        material_id="neon",
    ),
    op(
        "c4",
        "create_plane",
        "sign",
        name="Sign",
        size=[0.4, 0.3],
        location=[0, 0.3, 1.2],
        rotation=[90, 0, 0],
        material_id="poster",
    ),
    op(
        "l1",
        "add_light",
        "key",
        name="Key Light",
        light_type="area",
        location=[2, -2, 3],
        look_at=[0, 0, 0.75],
        energy=600,
        radius=1.0,
        color="#FFF1DD",
    ),
    op("l2", "add_light", "sun", name="Sun", light_type="sun", rotation=[45, 0, 30], energy=2.0),
    op(
        "l3",
        "add_light",
        "spot",
        name="Spot",
        light_type="spot",
        location=[0, 0, 2.5],
        look_at=[0, 0, 0],
        energy=300,
        spot_angle=30,
    ),
    op("k1", "add_camera", "cam_front", name="Front Camera", location=[0, -3, 1.4], look_at=[0, 0, 0.8], lens=40),
    op("w1", "set_world_background", None, color="#30343C", strength=0.6),
)


@pytest.fixture(scope="module")
def look(cfg, tmp_path_factory):
    image_id = uuid.uuid4()
    rel = f"projects/{USER}/{PROJECT}/textures/{image_id}.png"
    file = cfg.data_root / rel
    file.parent.mkdir(parents=True, exist_ok=True)
    tiny_png(file)
    batch = B(*LOOK["operations"], op("t1", "assign_texture", None, material_id="poster", image_id=str(image_id)))
    ops = canonical(batch)
    spec = make_spec(ops, thumbnail=True).model_copy(
        update={"textures": [TextureFile(image_id=image_id, path=rel, sha256=sha256_file(file))]}
    )
    result = execute_job(spec, cfg)
    assert result.success, (result.error_code, result.error_message, result.log_tail[-1500:])
    return result, image_id


def test_materials_textures_lights_inventory(look):
    result, image_id = look
    inv = result.inventory
    mats = {m.material_id: m for m in inv.materials}
    assert mats["walnut"].texture.pattern == "wood" and mats["walnut"].texture.scale == 2.0
    assert mats["walnut"].texture.color_a == "#6B4423"
    assert mats["glass"].transmission == pytest.approx(1.0) and mats["glass"].ior == pytest.approx(1.5)
    assert mats["neon"].emission_strength == pytest.approx(4.0) and mats["neon"].emission_color == "#FF2BD6"
    assert mats["poster"].texture.image_id == str(image_id)
    objs = inv.object_map()
    assert objs["key"].type == "LIGHT" and objs["key"].light.light_type == "area"
    assert objs["key"].light.color == "#FFF1DD" and objs["key"].light.energy == pytest.approx(600)
    assert objs["spot"].light.spot_angle == pytest.approx(30, abs=1e-3)
    assert objs["cam_front"].type == "CAMERA" and objs["cam_front"].camera.active
    assert objs["cam_front"].camera.lens == pytest.approx(40)
    assert inv.world.color == "#30343C" and inv.world.strength == pytest.approx(0.6)


def test_glb_has_textures_cameras_lights(look, cfg):
    result, _ = look
    staging = next(cfg.data_root.glob(f"projects/*/*/staging/{result.job_id}"))
    doc = glb_json(staging / "scene.glb")
    assert len(doc.get("images", [])) == 2  # wood pattern + uploaded poster, embedded
    assert doc.get("cameras") and "KHR_lights_punctual" in doc.get("extensionsUsed", [])
    assert "KHR_materials_transmission" in doc.get("extensionsUsed", [])
    assert glb_node_object_ids(staging / "scene.glb") == set(result.inventory.object_map())
    textured = {i for i, m in enumerate(doc["materials"]) if "baseColorTexture" in m.get("pbrMetallicRoughness", {})}
    prims = [p for m in doc["meshes"] for p in m["primitives"] if p.get("material") in textured]
    assert len(prims) == 2 and all("TEXCOORD_0" in p["attributes"] for p in prims)  # box-projected UVs


# --------------------------------------------------------------------------- render and export jobs
def derived_spec(kind: str, source: SourceVersion, **settings) -> BlenderJobSpec:
    job_id = uuid.uuid4()
    return BlenderJobSpec(
        job_id=job_id,
        kind=kind,
        project_id=PROJECT,
        user_id=USER,
        target_version_number=source.version_number,
        source=source,
        staging_dir=f"projects/{USER}/{PROJECT}/staging/{job_id}",
        operations=[],
        batch_sha256=batch_digest([]),
        render_thumbnail=False,
        timeout_seconds=300,
        render=RenderSettings(**settings) if kind == "render" else None,
        export=ExportSettings(**settings) if kind == "export" else None,
    )


@pytest.fixture(scope="module")
def look_source(cfg, look):
    return promote(cfg, look[0], 5)


def staged(cfg, result) -> Path:
    return next(cfg.data_root.glob(f"projects/*/*/staging/{result.job_id}"))


def test_render_job_uses_scene_camera(cfg, look_source):
    before = sha256_file(cfg.data_root / look_source.blend_path)
    r = execute_job(derived_spec("render", look_source, quality="draft", width=320, height=180), cfg)
    assert r.success, (r.error_code, r.error_message, r.log_tail[-1500:])
    assert [a.kind for a in r.artifacts] == ["render"]
    png = (staged(cfg, r) / "render.png").read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", png[16:24]) == (320, 180)
    assert r.details["camera_object_id"] == "cam_front" and r.details["scene_lights"] == 3
    assert r.details["samples"] == 16
    assert sha256_file(cfg.data_root / look_source.blend_path) == before  # the version is never modified


def test_render_job_unknown_camera(cfg, look_source):
    r = execute_job(
        derived_spec("render", look_source, quality="draft", width=64, height=64, camera_object_id="nope"), cfg
    )
    assert not r.success and r.error_code == "unknown_camera"


@pytest.mark.parametrize("fmt", ["stl", "obj", "gltf"])
def test_export_jobs(cfg, look_source, fmt):
    r = execute_job(derived_spec("export", look_source, format=fmt, unit="mm"), cfg)
    assert r.success, (r.error_code, r.error_message, r.log_tail[-1500:])
    out = staged(cfg, r) / r.artifacts[0].filename
    if fmt == "stl":
        data = out.read_bytes()
        tris = struct.unpack("<I", data[80:84])[0]
        assert tris > 100 and len(data) == 84 + 50 * tris
        xs = [struct.unpack("<f", data[84 + 50 * i + 12 : 84 + 50 * i + 16])[0] for i in range(min(tris, 400))]
        assert max(abs(x) for x in xs) > 100  # millimetres
    else:
        names = zipfile.ZipFile(out).namelist()
        if fmt == "obj":
            assert {"scene.obj", "scene.mtl"} <= set(names) and any(n.endswith(".png") for n in names)
        else:
            assert "scene.gltf" in names and any(n.endswith(".bin") for n in names)

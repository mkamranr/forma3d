# Modeling DSL v1.0

Nothing reaches Blender except validated DSL. There's no `exec`, no generated Python, and no
file paths from clients. The contract lives in `shared/forma3d_shared/schemas/modeling_operation.py`
(strict pydantic, `extra="forbid"`), and the semantic checks live in `shared/forma3d_shared/validation/validator.py`.
Inside Blender, the bl_runtime registry (`blender-worker/app/bl_runtime/operation_registry.py`)
handles exactly the same set of operations. `tests/test_registry_parity.py` checks that the two stay in sync.

## Conventions

- Units are meters and degrees, and Z points up.
- Create-op `location` is world space. `rotation` is local XYZ Euler in degrees.
- `set_dimensions` resizes the mesh data, so the object scale stays at 1.
- Object, material and collection ids match `^[a-z][a-z0-9_]{0,63}$`. They are stable: Blender stores them as custom properties
  (`app_object_id`, …) and exports them to glTF `extras`, so the viewport can map selections back to them.
- Free-text names reject code-like or path-like content (backticks, `__`, `import`, `bpy.`, `../`, absolute paths, …).
- Numeric bounds: coordinates ±500 m, dimensions 0.5 mm–100 m, angles ±3600°, scale factors 0.001–1000.

## Batch

```json
{
  "schema_version": "1.0",
  "summary": "Create a modern wooden desk",
  "operations": [
    {"operation_id": "op_mat_top", "operation_type": "create_material",
     "parameters": {"material_id": "oak", "name": "Oak", "base_color": "#C99563", "roughness": 0.6}},
    {"operation_id": "op_desk_top", "operation_type": "create_cube", "target_object_id": "desk_top",
     "dependencies": ["op_mat_top"],
     "parameters": {"name": "Desk Top", "dimensions": [1.6, 0.8, 0.04], "location": [0, 0, 0.74], "material_id": "oak"},
     "metadata": {"component": "tabletop"}}
  ]
}
```

`dependencies` can only point at earlier operations in the same batch. `metadata.component` sets the object's `component_type`.
Full examples are `forma3d_shared.examples.desk_batch()` and `black_metal_legs_batch()`.

## Operations

`?` marks an optional parameter.

| Operation | `target_object_id` | Parameters |
|---|---|---|
| `create_cube` | required (new id) | name, location?, rotation?, parent_id?, collection_id?, material_id?, dimensions? |
| `create_sphere` | required (new id) | name, …common?, radius?, segments?, rings? |
| `create_cylinder` | required (new id) | name, …common?, radius?, depth?, vertices? |
| `create_cone` | required (new id) | name, …common?, radius_bottom?, radius_top?, depth?, vertices? |
| `create_plane` | required (new id) | name, …common?, size? |
| `create_torus` | required (new id) | name, …common?, major_radius?, minor_radius?, major_segments?, minor_segments? |
| `move_object` | required | location? \| offset? |
| `rotate_object` | required | rotation? \| delta? |
| `scale_object` | required | scale? \| factor? |
| `set_dimensions` | required | dimensions |
| `duplicate_object` | required | new_object_id, name, offset? |
| `delete_object` | required | recursive? (children block deletion unless set) |
| `rename_object` | required | name |
| `parent_object` | required | parent_id, keep_transform? |
| `unparent_object` | required | keep_transform? |
| `set_visibility` | required | visible |
| `create_collection` | — | collection_id, name, parent_collection_id? |
| `move_to_collection` | required | collection_id |
| `create_material` | — | material_id, name, base_color?, metallic?, roughness?, alpha? |
| `assign_material` | required | material_id |
| `update_material` | — | material_id, name?, base_color?, metallic?, roughness?, alpha? |
| `set_base_color` / `set_metallic` / `set_roughness` / `set_transparency` | — | material_id + value |
| `bevel` | required (mesh) | width?, segments? (≤ 10), limit_method?, angle? |
| `solidify` | required (mesh) | thickness?, offset? |

### Phase 7 operations

| Operation | `target_object_id` | Parameters |
|---|---|---|
| `create_circle` | required (new id) | name, …common?, radius?, vertices?, fill? |
| `create_mesh` | required (new id) | name, …common?, vertices (≤ 5000), faces (3–64 indices each, all in range) |
| `create_curve` | required (new id) | name, …common?, points, curve_type (`poly` \| `smooth`)?, cyclic?, thickness?, profile_resolution?, resolution? — swept to a tube mesh |
| `extrude` | required (mesh) | side (`top` `bottom` `front` `back` `left` `right`, front = −Y), distance (≠ 0) |
| `inset` | required (mesh) | side \| `all`, thickness, depth? |
| `boolean_union` / `boolean_difference` / `boolean_intersect` | required (mesh) | operand_id (a mesh without children), keep_operand?, solver (`exact` \| `fast`)? — applied; the operand is deleted unless kept |
| `subdivide` | required (mesh) | levels (≤ `max_subdivision_levels`), smooth? |
| `mirror` | required (mesh) | axes (unique), mirror_object_id?, merge?, bisect? |
| `array` | required (mesh) | count (≤ `max_array_count`), offset, merge? |
| `remesh` | required (mesh) | voxel_size (≥ 0.002; density guarded at run time), smooth_shading? |
| `decimate` | required (mesh) | ratio |
| `apply_transform` | required | location?, rotation?, scale? (booleans: bake into the mesh; children keep their place) |
| `set_origin` | required | mode (`geometry_center` \| `bounds_bottom` \| `world_origin` \| `point`), point? |
| `join_objects` | required (mesh) | object_ids (meshes; their children are re-parented) |
| `separate_objects` | required (mesh) | mode (`loose_parts` \| `material`), new_object_ids, names? — the largest part keeps the id |
| `add_camera` / `update_camera` | required (new / camera) | name?, location?, look_at \| rotation, lens (8–300 mm)?, set_active? |
| `add_light` / `update_light` | required (new / light) | name?, light_type (`point` `sun` `spot` `area`)?, location?, look_at \| rotation, color?, energy?, radius?, spot_angle? |
| `set_world_background` | — | color, strength? |
| `assign_texture` | — | material_id, pattern \| image_id, color_a? / color_b? (patterns only), scale?, rotation? |
| `remove_texture` | — | material_id |

`create_material` / `update_material` also accept `emission_color`, `emission_strength`, `transmission`
and `ior`. Patterns: wood, marble, noise, checker, brick, fabric, concrete, brushed_metal. `image_id` is
an uploaded texture (`POST /projects/{id}/textures`); file paths never appear in the DSL.

Export, thumbnail rendering and saving are system steps. The DSL can't invoke them. Renders and
OBJ/STL/glTF exports are separate jobs (`POST /projects/{id}/renders|exports`) that read a committed version.

## Validation

1. **Structure**: the schema, a known `operation_type`, and parameter types and bounds.
2. **Semantics**: the batch is replayed against the current head inventory. Each target must exist; created ids must be new; there can be no use-after-delete,
   parent cycles or children left behind; materials and collections must exist; modifiers apply to meshes only; and dependencies must be valid.
3. **Limits** (`MODELING_*` env vars): operations per batch, new objects, scene objects and materials, vertex
   estimates, and bevel segments.

The validator collects every issue instead of stopping at the first. The API returns them as
`422 {"code": "invalid_batch", "issues": [{"code", "message", "operation_id", "location"}]}`.
A valid batch is canonicalised and hashed (sha256). The worker recomputes the hash and refuses any batch where it doesn't match.

## Execution and versioning

1. `POST /api/v1/projects/{id}/modeling/execute` validates the batch, takes `lock:project:{id}` (a second submission gets 409 `project_busy`),
   stores the batch and job, and enqueues the job on `jobs:blender`. It returns 202 with `{job_id, operation_batch_id}`.
2. The worker opens the head `.blend` after verifying its checksum, applies the operations, validates the meshes, and saves `.blend`, `.glb`,
   `thumbnail.png`, `inventory.json` and `operations.json` into `staging/{job_id}`.
3. The backend checks the artifacts' checksums, renames `staging/{job_id}` to `versions/vNNNN`, and commits the version row,
   assets and the new head in one transaction. Any failure leaves the previous head untouched.
4. Progress streams over SSE at `GET /api/v1/jobs/{id}/events` (supports `Last-Event-ID` replay).

# Forma3D HTTP API

| Method | Path | Notes |
|---|---|---|
| GET | `/health`, `/health/ready` | liveness; readiness (PostgreSQL + Redis, workers/LLM/disk as info) |
| GET | `/metrics` | Prometheus text, internal network only, optional bearer `METRICS_TOKEN` |
| POST | `/api/v1/auth/register` · `login` · `refresh` · `logout` | access token in body; refresh token in httpOnly SameSite=strict cookie |
| GET | `/api/v1/auth/me` | |
| GET/POST | `/api/v1/projects` | `?q=&status=active\|archived\|all&sort=&limit=&offset=` |
| GET/PUT/DELETE | `/api/v1/projects/{id}` | owner-scoped; other users get 404 |
| POST | `/api/v1/projects/{id}/duplicate` | |
| POST | `/api/v1/projects/{id}/modeling/execute` | raw DSL batch → 202 `{job_id}`; 422 `invalid_batch` with `issues`; 409 `project_busy` |
| GET | `/api/v1/jobs/{id}` · POST `/api/v1/jobs/{id}/cancel` | |
| GET | `/api/v1/jobs/{id}/events` | SSE (`Last-Event-ID` replay) |
| GET | `/api/v1/projects/{id}/versions` · `/versions/{vid}` | detail includes the scene inventory |
| GET | `/api/v1/projects/{id}/versions/{vid}/assets/{kind}` | kind = glb, thumbnail, blend, inventory, operations; ownership-checked |
| GET | `/api/v1/projects/{id}/thumbnail` | current head thumbnail |
| GET | `/api/v1/projects/{id}/scene` | head version + inventory + active job |
| POST | `/api/v1/projects/{id}/scene/starter` | sample desk for an empty project |
| POST | `/api/v1/projects/{id}/scene/objects` | add a primitive `{kind, name?, location?}` |
| PATCH/DELETE | `/api/v1/projects/{id}/scene/objects/{object_id}` | `{name, location, rotation, dimensions, visible, material_id, light_energy, light_color, camera_lens, active_camera}`; `?recursive=true` |
| POST | `/api/v1/projects/{id}/scene/objects/{object_id}/material` | give one object its own material |
| PATCH | `/api/v1/projects/{id}/scene/materials/{material_id}` | edit a (possibly shared) material: colour, metallic, roughness, opacity, `transmission` (glass), `emission_strength` (glow), `texture` (pattern or `""`), `texture_image_id`, `texture_scale` |
| POST | `/api/v1/projects/{id}/versions/{vid}/restore` | move the head; next edit branches from it |
| POST | `/api/v1/projects/{id}/modeling/undo` · `redo` | head → parent / most recent child (409 `nothing_to_undo` / `nothing_to_redo`) |
| POST | `/api/v1/projects/{id}/versions/compare` | `{base_version_id, target_version_id}` → objects/materials added, removed, changed (per field) |
| GET | `/api/v1/projects/{id}/conversation` | conversation + messages (assistant metadata: `version_number`, `action: export`, ...) |
| POST/GET | `/api/v1/projects/{id}/exports` | `{format: glb\|gltf\|obj\|stl, unit: m\|cm\|mm, include_hidden, version_id?}` → 202 output job (GLB and cached settings return `succeeded` at once); 422 for other formats |
| GET | `/api/v1/exports/{id}` · `/exports/{id}/download` | status/progress; file as `name-vN[-unit].ext` (OBJ/glTF zipped with textures) |
| POST/GET | `/api/v1/projects/{id}/renders` | `{engine: cycles, quality: draft\|standard\|high, width, height, camera_object_id?, transparent_background}` → 202; `eevee` → 422 `engine_unavailable` unless enabled |
| GET | `/api/v1/renders/{id}` · `/renders/{id}/image` | PNG |
| POST/GET | `/api/v1/projects/{id}/textures` · `/textures/{texture_id}` | multipart PNG/JPEG upload (≤10 MB, header-checked); the DSL references it by `image_id` only |
| POST | `/api/v1/projects/{id}/conversation/messages` | `{content, selected_object_ids?, selected_object_id?, attachment_ids?}` → 202 `{message, job_id}`; follow `job_id` via SSE; 409 `project_busy`. `content` may be empty when photos are attached; 422 `attachment_not_found` / `too_many_attachments` (max 4) |
| POST/GET | `/api/v1/projects/{id}/references` · `/references/{ref_id}` | image-to-3D: multipart PNG/JPEG upload (≤10 MB, ≤4096 px, header-checked) of a photo to attach to a message; the user message's metadata then carries `attachments` and, once read, `reference_description` |


All scene edits compile to DSL and return 202 + `job_id`, exactly like `modeling/execute`.

## Internal endpoints (workers only)

| Method | Path | Notes |
|---|---|---|
| POST | `/internal/v1/jobs/{id}/start` · `complete` | worker callbacks, `X-Internal-Token`, internal network only |
| GET/POST | `/internal/v1/agent-jobs/{id}/references/{ref_id}` · `reference-description` | Vision agent: bytes of a photo attached to this job only; store its description on the message |
| GET/POST | `/internal/v1/agent-jobs/{id}/context` · `plan` · `runs` · `restore` · `complete` | agent worker: context, validated plan (per attempt, `defer_commit`), agent run records, undo/restore, reply |
| GET/POST | `/internal/v1/jobs/{id}/staged` · `commit` · `discard` | agent worker: inspect a staged Blender result, then commit it as a version or discard it |

DSL reference: [`modeling-dsl.md`](modeling-dsl.md). Try it after `make up` (token from login):

```bash
python -c "import json;from forma3d_shared.examples import desk_batch;print(json.dumps(desk_batch()))" > desk.json
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  --data @desk.json http://<docker-host>:3000/api/v1/projects/$PROJECT/modeling/execute
```

Interactive docs at `/api/docs` when `APP_ENV != production`.


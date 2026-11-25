# Architecture decisions

Living record of decisions beyond `project-specification.md`. Update as phases land.

## Phase 1

- **Monorepo root** is `forma3d/` (spec §19 calls it `3d-studio/`). Python images build with the
  repo root as context so `shared/` can be installed into every service.
- **Auth**: argon2 password hashing (pwdlib). 15-minute HS256 JWT access tokens held in memory by the
  SPA. Opaque refresh tokens (sha256-hashed in `refresh_tokens`) in an httpOnly, SameSite=strict cookie
  scoped to `/api/v1/auth`. Each refresh rotates the token; presenting a rotated/revoked token revokes
  the entire login family (theft detection). Logout revokes the family.
- **Ownership**: every project query filters on `user_id`; cross-user access returns 404 to avoid
  disclosing ids.
- **Deletion**: projects are soft-deleted (`status='deleted'`, `deleted_at`). A retention job will purge
  files and rows (Phase 8).
- **Schema**: all §11 tables are created in migration `0001` (plus `refresh_tokens`, and
  `scene_versions.message_id` to link a version to its conversation message). The circular FK
  `projects.current_version_id → scene_versions.id` is added after both tables exist.
  Types are portable (`Uuid`, JSON→JSONB on PostgreSQL) so unit tests run on SQLite.
- **Rate limiting**: fixed-window counters in Redis (`ratelimit:*`), applied to auth endpoints now and
  job submission later.
- **Logging**: structlog JSON with a correlation id (`X-Request-ID`, validated or generated) bound per
  request and echoed in the response; secret-like keys are redacted.
- **Networking**: `edge` (published frontend), `app` (internal: nginx↔backend), `data` (internal:
  backend, workers, postgres, redis), `llm` (egress for agent-worker only). Blender worker has no egress.
- **Frontend**: the prototype's single `App.tsx` was split into `components/*`, `features/*`,
  `stores/*`; its CSS was split into `src/styles/*.css` with byte-identical rules (verified).
  Google Fonts were replaced by `@fontsource` packages; drei's CDN HDRI preset was replaced with local
  Lightformers. The workspace route is code-split so three.js is not in the auth/dashboard bundle.
  Original prototype files are kept in `docs/prototype/` for reference.

## Phase 2 — Blender worker, DSL v1, versioned commits

- **DSL**: 27 allowlisted operations as a strict pydantic discriminated union (`shared/`); see
  `docs/modeling-dsl.md`. The validator replays a batch against the head inventory symbolically and
  collects all issues; valid batches are canonicalised and SHA-256 hashed.
- **Two-process worker**: `app/worker.py` (Python 3.12, pydantic, Redis) consumes `jobs:blender`
  (consumer group `blender-workers`, XAUTOCLAIM for crashed consumers) and launches
  `blender --background --factory-startup --disable-autoexec --python-exit-code 4 --python bl_runtime/main.py`.
  `bl_runtime` is stdlib + `bpy` only (Blender bundles no pydantic) and dispatches through a handler
  dict; a parity test pins it to the shared allowlist. Blender 4.5.17 LTS, tarball SHA-256 verified at build.
- **Job lifecycle**: worker → `POST /internal/v1/jobs/{id}/start` (dedupe: `proceed=false` unless
  queued) → Blender → `/complete` with `BlenderJobResult`. Messages are ACKed only after `/complete`
  succeeds, so a crashed worker's job is reclaimed. `/internal` is token-protected
  (`X-Internal-Token`, constant-time compare), not proxied by nginx, and only on internal networks.
- **Isolation**: each job runs in its own workspace under `/tmp/forma3d-jobs` (tmpfs); the source
  `.blend` checksum is verified before opening; Blender has a timeout, process-group SIGTERM→SIGKILL,
  cancellation via the `cancel:{job_id}` Redis flag, and redacted log tails.
- **Atomic commit** (`backend/app/services/version_service.py`): check expected head + lock holder,
  verify artifact sizes/SHA-256, `os.replace(staging, versions/vNNNN)`, single DB transaction
  (version, assets, head inventory rows, project head + thumbnail, batch/job status); on DB failure the
  directory is renamed back. A retried `/complete` re-adopts an already promoted directory after
  re-verifying checksums. Startup `reconcile_storage` quarantines version dirs without DB rows and
  removes stale staging dirs.
- **Concurrency**: `lock:project:{id}` (SET NX EX, compare-and-delete release) — one active modeling
  job per project; `409 project_busy` otherwise.
- **Events**: Redis stream `events:{job_id}` (maxlen 1000, 24 h TTL after terminal); the SSE endpoint
  replays from `Last-Event-ID`, pings every 15 s and closes after a terminal event.
- **Assets**: served only by kind via ownership-checked routes; storage paths are never returned.

## Phase 3 — Interactive workspace on real scenes

- **One write path**: inspector, hierarchy and gizmos call `/scene/*` endpoints
  that compile edits into small DSL batches and reuse `ModelingService.submit_batch` — same validator,
  project lock, Blender job and immutable version as everything else. No client-side scene copy.
- **Server state** lives in TanStack Query (`scene`, `versions`, `model:{versionId}`); Zustand holds only
  UI state (selection, tool, shading, projection, active job). GLBs are cached forever per version id
  because versions are immutable.
- **Assets with auth**: GLB/thumbnail routes require the bearer token, so the client fetches them
  (`apiFetch`) and parses with `GLTFLoader.parseAsync` / object URLs — no tokens in URLs.
- **Stable-ID picking**: glTF extras land in `userData.app_object_id`; clicks walk up to the nearest
  ancestor with an id and skip hidden objects (hidden objects stay in the GLB so ids remain complete).
- **Coordinates**: API values are Blender space (Z up, meters, world location, local Euler degrees);
  conversion to three.js (Y up) happens only in the viewport (`utils/coords.ts`).
- **Gizmos**: drei `TransformControls` preview locally; release commits `move_object {location}` or
  `set_dimensions`. Failed/cancelled jobs rebuild the scene from the cached GLB. Fit-to-view measures
  model meshes only (the gizmo's helper plane is effectively infinite).
- **Job tracking**: fetch-based SSE (bearer header, `Last-Event-ID` reconnect, stops at terminal
  events) with polling fallback; a job already running when the page loads is resumed from `/scene`.
- **Materials**: shared materials are edited in place (UI says “shared by N objects”); “Make unique”
  creates a per-object copy via `create_material` + `assign_material`.
- **Restore** moves `projects.current_version_id` (under the project lock); the next edit's
  `parent_version_id` is the restored version, so history branches instead of being rewritten.
- **Timestamps**: `UTCDateTime` returns aware datetimes on every dialect (SQLite dropped tzinfo).
- **Local runner**: `deployment/scripts/dev_local.py` runs the real API + a worker thread with real
  Blender over an in-process fakeredis — used for the browser verification of this phase.

## Phase 4 — LLM integration and the conversational vertical slice

- **Flow**: `POST /conversation/messages` stores the user message and creates an `agent` job on
  `jobs:agent` (409 if any job is active for the project). The agent worker claims it
  (`/internal/v1/jobs/{id}/start`, deduped), loads an `AgentContext` (head inventory, last 12 turns,
  selected object), plans, and submits through `/agent-jobs/{id}/plan` → `ModelingService.submit_batch`:
  the same validator, project lock, Blender job and version commit as UI edits. It relays the modeling
  job's `blender.*` / `version.created` events onto the agent job's stream, so the browser follows a
  single job; `/complete` then writes the assistant message linked to the new version.
- **Planner** (`agent-worker/app/planner.py`): one structured call returning
  `{intent, reply, summary, operations}` with intents `modeling | question | clarify | export`. The
  prompt embeds a compact scene (ids, transforms, materials with use counts) and an operation
  catalogue generated from the shared pydantic schemas (a test asserts every DSL operation is
  documented). Output goes through JSON extraction/repair → `LLMResponse` → `validate_batch` against
  the head inventory; parse or validation issues are fed back for at most `AGENT_MAX_RETRIES` repairs.
  The validator stays the trust boundary: LLM output never reaches Blender unchecked.
- **Providers**: `OpenAICompatibleProvider` (httpx; `response_format` json_schema / vLLM `guided_json`
  / json_object / none; retries 5xx, 429 and timeouts, fails fast on other 4xx) and `FakeProvider`, a
  deterministic offline planner for CI and demos. Every call is recorded in `agent_runs` (model,
  duration, tokens, validation issues).
- **Workers**: consumer group `agent-workers`; ACK only after `/complete`; un-ACKed jobs are reclaimed
  with `XAUTOCLAIM` and `/start` dedupes. Cancelling the agent job is forwarded to its modeling job.
  A busy project at submit time is retried briefly.
- **Ordering**: message timestamps are strictly increasing per conversation (Windows clocks tick
  every ~15 ms); a page reload during a turn resumes the agent job rather than its modeling job.
- **Exports**: an `export` reply pins `export_version_id` (the head when asked), so "Download GLB"
  serves the exported version even after later edits.
- **Redis timeouts**: blocking reads use 2 s windows and treat a client `TimeoutError` as an empty read
  (both workers and SSE); SSE sends a comment ping every 15 s.
- **Local runner**: `dev_local.py` starts a fakeredis **TCP** server and runs the real `blender-worker`
  and `agent-worker` processes against the real API, i.e. the same code paths as Docker.

## Phase 5 — LangGraph multi-agent workflow

- **Graph** (`agent-worker/app/graph/workflow.py`, spec §7.2): `load_context → inspect_scene →
  conversation →` {`answer` | `version_manager` | `ask → wait_answer` | `planning → modeling → execute →
  quality →` {`commit` | `revise → modeling …` | `fail`}} `→ respond`. One thread per conversation
  (`conversation.langgraph_thread_id`); the agent job id travels in `config.configurable.job_id`.
- **Agents**: *Conversation* (LLM: intent, task, targets, clarify), *Scene Inspection* (deterministic:
  world bounding boxes, material users, summary; always runs before anything is changed), *3D Planning*
  (LLM: a component plan — parts, materials, spatial relations — not raw DSL), *Modeling*
  (deterministic compiler plan → DSL + expectations, then the shared validator), *Quality*
  (deterministic: expected objects/materials, untouched objects identical, relations `on_top_of` /
  `under` / `attached_to` / `inside` / `beside`, floating and below-floor parts — each failing check
  carries a concrete fix hint), *Revision* (LLM: a minimal patch to the plan from the QA issues).
  Each LLM agent may use its own model (`LLM_MODEL_<AGENT>`); invalid output is repaired at most
  `AGENT_MAX_RETRIES` times with the concrete problems fed back, then rejected (nothing reaches Blender).
- **Staged commit**: agent builds are submitted with `defer_commit=true`. Blender's result is
  verified and *staged* (job status `staged`, `job.staged` event, project lock kept); QA reads it
  through `/internal/v1/jobs/{id}/staged`, then the agent commits (`/commit`, creates the version)
  or discards it (`/discard`). Only a passing attempt — or, after `AGENT_MAX_REVISIONS`, one whose
  remaining problems are fixable geometry warnings — becomes a version; errors roll the turn back.
  Every attempt rebuilds from the head, so revisions never stack partial results.
- **Clarification**: `ask` completes the current job with the question; `wait_answer` calls
  `interrupt()`. The next user message on the conversation resumes the thread with
  `Command(resume=…)`; the Conversation Agent sees the question and original request.
- **Persistence and resume**: invoked with `durability="sync"`, so every super-step is checkpointed
  (SQLite in dev, Postgres `langgraph` schema in compose). Nodes are idempotent towards the backend:
  plan submissions are keyed by attempt, commit/discard/complete are idempotent. A worker that dies
  leaves its stream entry un-ACKed; live workers refresh their entry (XCLAIM JUSTID heartbeat), so
  only dead workers' entries go idle and are reclaimed (`/start` with `reclaim=true`), and the
  turn continues from its last checkpoint without repeating finished Blender builds.
- **Exclusivity**: while an agent turn is queued or running, UI edits and UI restores return
  409 `project_busy`, so the head cannot move under a staged attempt.
- **Events**: `agent.started` (per agent), `agent.progress`, `plan.created`, `plan.validated`,
  relayed `blender.*`, `quality.completed`, `version.created`, then the terminal job event. The
  assistant message carries `metadata.quality` (`passed`, `checks`, `revisions`, `warnings`), shown
  as a chip in the chat.
- **Tests**: `agent-worker/tests/sim.py` simulates the internal API and DSL geometry so the whole
  graph runs in-process: §23 scenario, Scenario C, QA failure → revision, bounded failure,
  invalid-plan rejection, undo/restore/questions, cancel, crash at three points + resume on a new
  worker with a SQLite checkpoint, stream reclaim.

## Phase 6 — Conversational editing and versioning

- **Undo / redo** (`app/services/history_service.py`): pointer moves over the immutable version tree.
  Undo = head's parent; redo = head's most recent child. A change made after an undo/restore branches
  (its `parent_version_id` is the restored version). Exposed as `POST /modeling/undo|redo` (header
  buttons, Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y outside text fields) and as the agent's `undo` / `redo` /
  `restore` intents (`RestoreRequest{undo|redo|version_number}`). Same guards as restore: blocked while
  a job builds or an agent turn runs.
- **Compare** (`forma3d_shared/inventory_diff.py`): objects and materials matched by stable id; per-field
  before/after with a float tolerance (float32 round trips), so a rename is a change, not delete + add.
  `POST /versions/compare`; the history dialog compares any two ticked versions (older → newer).
- **Conversation ↔ version**: versions keep `message_id`; the list returns `source` (`chat` / `edit`) and
  the user's `prompt`. The dialog shows the prompt and a "Chat" link that scrolls to and highlights the
  message; "Saved as vN" chips in the chat open the history focused on that version. Branches show
  "from vN" when a version's parent is not the version before it.
- **Selection context**: Shift/Ctrl/Cmd-click selects several objects in the hierarchy or viewport
  (primary = last, which drives gizmos and the properties panel). Messages carry
  `selected_object_ids`; the Conversation and Planning agents see "Selected objects: 'Name' (id …)".
- **Minimal changes**: the Planning Agent only names parts it changes; the Modeling compiler emits ops
  only for those; QA fails any untouched object that differs (transform, dimensions, materials, parent,
  visibility). Recolouring one part of a shared material assigns a new material to that part only.
- **Scenario E (concurrency)**: job admission is atomic per project — chat posts and UI edits take a short
  Redis admission mutex around "nothing running? → create job", so two simultaneous requests cannot
  both be accepted (one gets 409 `project_busy`). The project lock serialises builds, and the commit
  still checks `expected_head_version_id`, so even a lost lock cannot overwrite a newer version.
- **Tests**: backend `test_history.py` (undo/redo/branching, compare, prompts, selection, concurrent
  messages/edits, stale-head commit); agent `test_editing.py` (Scenarios A and B, redo, multi-selection);
  frontend `History.test.tsx`; end to end with real Blender (FakeProvider and vLLM) including a reload
  after restarting the stack.

## Phase 7 — Advanced modeling and rendering

- **DSL v1.0, 51 operations** (`forma3d_shared/schemas/modeling_operation.py`, still additive):
  `create_circle` / `create_mesh` (validated index lists, ≤5000 vertices) / `create_curve` (swept to a tube
  mesh so every modelled object stays a MESH); `extrude` / `inset` by side; `boolean_union|difference|intersect`
  (applied via the evaluated mesh; the operand is consumed unless `keep_operand`); `subdivide`, `mirror`,
  `array`, `remesh`, `decimate` (limits: subdivision ≤3, array ≤100, remesh density guard); `join_objects`,
  `separate_objects` (loose parts / by material, deterministic new ids), `apply_transform`, `set_origin`;
  `add/update_camera`, `add/update_light`, `set_world_background`; `assign_texture` / `remove_texture`;
  materials gain emission, transmission and IOR. The validator knows object kinds (`not_a_mesh`,
  `not_a_camera`, `not_a_light`) and checks boolean operands (`invalid_operand`, `has_children`), modifier
  limits and join/separate ids. `HANDLERS == ALLOWED_OPERATIONS` is tested.
- **Textures**: procedural patterns (wood, marble, noise, checker, brick, fabric, concrete, brushed metal)
  are generated with numpy as tileable 512 px images and packed into the .blend, so they embed in GLB and
  export with OBJ/glTF. Uploaded images are `Asset(asset_type="texture", version_id=None)`; the DSL only
  carries `image_id`, the backend resolves it to a path + sha256 (`spec.textures`) and the worker copies
  checksum-verified files into the job workspace, so Blender never sees user paths. Box-projected UVs
  (meters × scale) are regenerated for touched textured objects after each batch.
- **Derived jobs** (`BlenderJobSpec.kind = render | export`): read an immutable version, no operations, no
  project lock, so renders/exports never block editing or chat. Outputs are `Asset` rows on the source
  version (`render`, `export_{fmt}`) under `derived/{job_id}/`, validated (PNG header, zip, binary STL size)
  and cached by their settings key. `max_pending_outputs` per user bounds the queue.
- **Renders**: Cycles CPU with three profiles; uses the requested camera, else the active scene camera,
  else an auto-framed one; adds a studio rig only when the scene has no lights; honours the world
  background. Thumbnails also respect user lights.
- **Agents**: plans can `cut_into` a part (compiled to an unparented cutter + `boolean_difference`; QA
  expects the cutter gone), add `light` parts, and give materials `texture` / `emission` / `transmission`.
  QA ignores non-mesh objects for support/contacts. `export` takes a format; the new `render` intent starts a
  render job from chat (`metadata.output_job_id`, polled by the chat chip).
- **World-space rotations**: the inventory's `rotation` is local to the parent (what `rotate_object` and the
  UI edit) while `location` is world space. Inspection and QA compose the parent chain into world rotations,
  and the compiler converts a planned world rotation into a local one for parented objects. Found by the
  office chair end to end (casters hang under rotated star legs); the test simulator now models Blender's
  keep-transform parenting.
- **Frontend**: export dialog (format, unit, hidden objects) and render dialog (quality, size, camera,
  transparent background, recent renders); chat chips with progress → download / image preview; light and
  camera markers in the viewport (glTF punctual lights are muted, the studio lighting stays); light/camera
  properties; glass/glow sliders; texture picker with image upload; add light / add camera tools.
- **Tests**: shared `test_advanced_ops.py`; Blender `test_blender_advanced.py` (real Blender: booleans,
  extrude/inset, modifiers, join/separate, curves, textures, cameras/lights, every export format, render);
  backend `test_outputs.py`; agent `test_advanced.py` (cabinet, office chair, hole, light, glass, texture,
  export/render intents, world rotations); frontend `Outputs.test.tsx`. End to end with real Blender
  (FakeProvider and vLLM `agents-a1`).

## Phase 8 — Hardening and operations

- **Job recovery**: `backend/app/services/maintenance.py` runs a leader-elected loop (`SET NX EX` on
  `maintenance:leader`) that fails jobs past their deadline (`job_deadline`: queued / staged / running per
  type, plus grace), discards abandoned staged results, releases project locks with no active job and
  applies retention. Agent timeouts go through `ConversationService.complete`, so the chat gets a reply.
- **Reclaim without double work**: Blender workers refresh their in-flight stream entry (`XCLAIM ... JUSTID`)
  from the heartbeat, so `XAUTOCLAIM` only takes entries of dead workers. `POST /internal/v1/jobs/{id}/start`
  with `reclaim=true` re-runs a `running` job only when the previous worker's heartbeat key is gone
  (`reason=rerun`, `diagnostics.reruns`), otherwise answers `running_elsewhere` and the entry stays pending.
- **Secrets**: `forma3d_shared.secrets` reads `NAME_FILE`, then `$SECRETS_DIR/name` (`/run/secrets`); the
  database password secret is injected into the SQLAlchemy and checkpoint URLs. Production refuses default
  or short secrets. Blender subprocesses get an allowlisted environment (`blender_env`).
- **Observability**: `/metrics` (Prometheus text, optional bearer token, internal only) with HTTP metrics by
  route template, job/queue/worker/LLM/disk gauges; `/health/ready` reports `degraded` with reasons. Agent
  workers publish `workers:agent:{id}:llm` from a `GET /models` probe.
- **Containers**: read-only root filesystems, tmpfs scratch, `cap_drop: ALL`, `no-new-privileges`, PID and
  CPU/memory limits, rotated JSON logs, replica counts from `.env`. Overlays for file secrets, NVIDIA GPU
  rendering (`F3D_CYCLES_DEVICE`, CPU fallback) and Prometheus.
- **Operations scripts** (`deployment/scripts/`): `generate-secrets.sh`, `backup.sh` / `restore.sh`
  (pg_dump -Fc + volume tar, checksummed), `offline-bundle.sh` / `offline-install.sh` (docker save +
  `images.lock` with image IDs, verified on load).
- **Frontend**: security headers + CSP in nginx, `/internal/` and `/metrics` blocked; code split so the
  dashboard loads ≈85 kB gzip and three.js (`vendor-3d`) only with the workspace; expired renders/exports
  offer a re-run.
- **Tests**: backend `test_hardening.py` (sweeper, reclaim, locks, retention, leader, metrics, secrets);
  blender worker reclaim / keepalive / environment tests.

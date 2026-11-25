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

- **One write path**: inspector, hierarchy, gizmos and the (rule-based) chat call `/scene/*` endpoints
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

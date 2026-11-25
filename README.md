# Forma3D — Conversational AI 3D Studio

Describe an object in natural language; an LLM plans it, a validated modeling DSL drives
Blender (headless, in Docker), and the browser shows the GLB in a Three.js viewport. Refine it
through conversation with full version history.

Full specification: [`project-specification.md`](project-specification.md).

## Status

| Phase | Scope | State |
|---|---|---|
| 1 | Infrastructure, auth, project CRUD, dashboard, health checks | **Done** (pending first run on the Docker host) |
| 2 | Blender worker, modeling DSL v1, validator, GLB export, versioned commits | **Done** (pending first run on the Docker host) |
| 3 | Interactive workspace wired to real scenes | **Done** (pending first run on the Docker host) |
| 4 | LLM integration (vLLM) — end-to-end vertical slice | Next |
| 5–8 | LangGraph agents, conversational editing, advanced modeling, hardening | — |

## Repository layout

```
frontend/        React + TS + Vite (R3F viewport, TanStack Query, Zustand, Tailwind for new screens)
backend/         FastAPI, SQLAlchemy 2 (async), Alembic, JWT + rotating refresh cookies
agent-worker/    LangGraph agent worker (Phase 1: heartbeat stub)
blender-worker/  Redis-stream consumer + headless Blender 4.5 runtime (bl_runtime: stdlib + bpy)
shared/          forma3d_shared: modeling DSL, validator, job/event/inventory contracts
deployment/      docker-compose.yml, docker-compose.dev.yml, scripts
docs/            Architecture notes, original prototype reference
```

## Running the stack (Docker host)

Docker runs on a separate host. From this machine either point the Docker CLI at it:

```bash
docker context create forma3d-remote --docker "host=ssh://user@docker-host"
docker context use forma3d-remote
```

…or copy the repo to the host and run the same commands there.

```bash
make env        # creates .env from .env.example — then edit every "change-me" value
make up         # build + start, waits for health checks (runs DB migrations first)
make ps
open http://<docker-host>:3000
```

Services: `frontend` (nginx, :3000 → SPA + `/api` proxy), `backend` (FastAPI :8000, internal),
`migrate` (one-shot `alembic upgrade head`), `agent-worker`, `blender-worker`, `postgres`, `redis`.
Only the frontend port is published; postgres/redis/workers are on internal networks, and only
`agent-worker` has egress (for the LLM endpoint).

## Local development (no Docker)

Backend (Python 3.10+ locally; containers use 3.12):

```bash
cd backend
python -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt   # Windows path
.venv/Scripts/pip install -e ../shared
.venv/Scripts/python -m pytest -q          # SQLite + fakeredis, no services needed
```

Shared contracts and Blender worker (a local Blender 4.5 install enables the integration tests):

```bash
cd shared && ../backend/.venv/Scripts/python -m pytest -q
cd blender-worker && BLENDER_BIN=/path/to/blender ../backend/.venv/Scripts/python -m pytest -q
cd backend && BLENDER_BIN=/path/to/blender .venv/Scripts/python -m pytest -q tests/test_e2e_blender.py
```

Run one job spec without Redis/backend: `cd blender-worker && python -m app.run_once spec.json`.

**Whole app without Docker** (SQLite + in-process fake Redis + real Blender; dev/demo only):

```bash
BLENDER_BIN=/path/to/blender backend/.venv/Scripts/python deployment/scripts/dev_local.py   # API on :8000
cd frontend && npm run dev                                                                    # UI on :5173
```

Frontend (Node 20):

```bash
cd frontend
npm ci
npm test           # Vitest
npm run build
VITE_API_PROXY=http://localhost:8000 npm run dev
```

## API

| Method | Path | Notes |
|---|---|---|
| GET | `/health`, `/health/ready` | readiness checks PostgreSQL + Redis |
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
| PATCH/DELETE | `/api/v1/projects/{id}/scene/objects/{object_id}` | `{name, location, rotation, dimensions, visible, material_id}`; `?recursive=true` |
| POST | `/api/v1/projects/{id}/scene/objects/{object_id}/material` | give one object its own material |
| PATCH | `/api/v1/projects/{id}/scene/materials/{material_id}` | edit a (possibly shared) material |
| POST | `/api/v1/projects/{id}/versions/{vid}/restore` | move the head; next edit branches from it |

All scene edits compile to DSL and return 202 + `job_id`, exactly like `modeling/execute`.
| POST | `/internal/v1/jobs/{id}/start` · `complete` | worker callbacks, `X-Internal-Token`, internal network only |

DSL reference: [`docs/modeling-dsl.md`](docs/modeling-dsl.md). Try it after `make up` (token from login):

```bash
python -c "import json;from forma3d_shared.examples import desk_batch;print(json.dumps(desk_batch()))" > desk.json
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json"      --data @desk.json http://<docker-host>:3000/api/v1/projects/$PROJECT/modeling/execute
```

Interactive docs at `/api/docs` when `APP_ENV != production`.

## Known limitations

- Blender runs CPU-only (Cycles thumbnail at 384 px, a few seconds per job); GPU compose is Phase 8.
- The Linux Blender image pins and checksum-verifies the official tarball but has not yet been built
  on the Docker host; all Blender tests so far ran against Blender 4.5.17 on Windows.
- The chat assistant is a **rule-based preview** (add / recolour / resize / hide / delete) that runs
  real edits through the pipeline; the LLM planner replaces it in Phase 4.
- Every edit is a Blender job (≈5–20 s each, CPU thumbnail included); edits are disabled while one runs.
- Gizmo resize previews scale children, but the committed `set_dimensions` only resizes the object's
  own mesh, so children snap back when the new version loads.
- Restore moves the head pointer; undo/redo shortcuts and version compare are Phase 6.
- Project delete is a soft delete; file purge/retention job is Phase 8.
- Duplicate still copies project metadata only (not versions); copying the head version is Phase 6.
- Image tags are pinned; digest pinning and the offline bundle are Phase 8.
- shadcn/ui is approximated with small hand-written Tailwind components (`frontend/src/components/ui`)
  rather than the shadcn CLI, to avoid Radix/preflight conflicts with the prototype styles.

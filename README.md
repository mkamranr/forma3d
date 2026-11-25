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
| 4 | LLM integration (vLLM) — conversational vertical slice (§23) | **Done**, verified against a real vLLM model |
| 5 | LangGraph multi-agent workflow (inspection, QA, clarification, revision, resume) | **Done**, verified against a real vLLM model |
| 6 | Conversational editing & versioning | Next |
| 7–8 | Advanced modeling, hardening | — |

## Repository layout

```
frontend/        React + TS + Vite (R3F viewport, TanStack Query, Zustand, Tailwind for new screens)
backend/         FastAPI, SQLAlchemy 2 (async), Alembic, JWT + rotating refresh cookies
agent-worker/    Agent worker: LLM providers (vLLM/OpenAI-compatible, offline fake), planner + repair loop
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

Agent worker (no LLM needed: tests use the deterministic `FakeProvider` and mocked HTTP):

```bash
cd agent-worker && ../backend/.venv/Scripts/python -m pytest -q
```

**Whole app without Docker** (SQLite + fakeredis TCP server + the real blender-worker and
agent-worker processes + real Blender; dev/demo only):

```bash
BLENDER_BIN=/path/to/blender backend/.venv/Scripts/python deployment/scripts/dev_local.py   # API on :8000
cd frontend && npm run dev                                                                    # UI on :5173
```

The agent uses the offline planner unless an LLM is configured, e.g.
`LLM_BASE_URL=http://gpu-host:8000/v1 LLM_MODEL=<served model name>` before `dev_local.py`.

## LLM configuration

The agent worker talks to any OpenAI-compatible `/chat/completions` endpoint (vLLM recommended).
Set in `.env`; the endpoint must be reachable from the `agent-worker` container:

| Variable | Meaning |
|---|---|
| `LLM_PROVIDER` | `vllm` / `openai_compatible`, or `fake` for the offline planner (CI, demos) |
| `LLM_BASE_URL` | e.g. `http://10.0.0.5:8000/v1` |
| `LLM_MODEL` | served model name (`vllm serve <name>`) |
| `LLM_MODEL_CONVERSATION`, `LLM_MODEL_PLANNING`, `LLM_MODEL_REVISION` | optional per-agent models on the same endpoint (fall back to `LLM_MODEL`) |
| `LLM_STRUCTURED_OUTPUT` | `json_schema` (vLLM >= 0.6 `response_format`), `guided_json` (older vLLM), `json_object`, `none` |
| `LLM_THINKING` | `auto` / `on` / `off`: vLLM `chat_template_kwargs.enable_thinking` for reasoning models (Qwen3...); `off` is far faster |
| `LLM_TEMPERATURE`, `LLM_MAX_TOKENS`, `LLM_TIMEOUT_SECONDS` | sampling and limits (defaults 0.2 / 8192 / 120 s) |
| `AGENT_MAX_RETRIES` | repair attempts per LLM agent call when its output fails to parse or validate (default 2) |
| `AGENT_MAX_REVISIONS` | QA → Revision Agent → rebuild loops per turn (default 2) |
| `CHECKPOINT_URL` | LangGraph checkpointer: `memory`, `sqlite:///file.db` (dev_local default), `postgresql://…` (compose default, `langgraph` schema) |

Whatever the model returns is parsed, schema-checked and run through the same DSL validator as
every other edit before Blender sees it. Invalid plans go back to the model with the validator's
issues; after the retries the user gets an explanation and the scene is unchanged. A
structured-output mode the server rejects fails fast with a clear error (`llm_unavailable`).

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
| GET | `/api/v1/projects/{id}/conversation` | conversation + messages (assistant metadata: `version_number`, `action: export`, ...) |
| POST | `/api/v1/projects/{id}/conversation/messages` | `{content, selected_object_id?}` → 202 `{message, job_id}`; follow `job_id` via SSE; 409 `project_busy` |

All scene edits compile to DSL and return 202 + `job_id`, exactly like `modeling/execute`.
| POST | `/internal/v1/jobs/{id}/start` · `complete` | worker callbacks, `X-Internal-Token`, internal network only |
| GET/POST | `/internal/v1/agent-jobs/{id}/context` · `plan` · `runs` · `restore` · `complete` | agent worker: context, validated plan (per attempt, `defer_commit`), agent run records, undo/restore, reply |
| GET/POST | `/internal/v1/jobs/{id}/staged` · `commit` · `discard` | agent worker: inspect a staged Blender result, then commit it as a version or discard it |

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
- LangGraph checkpoints are verified with SQLite (unit tests + `dev_local`); the Postgres checkpointer
  (compose default) has not yet run against a real Postgres (first Docker-host run).
- QA is geometric and deterministic (bounding boxes, relations, untouched objects); it does not judge
  aesthetics. A staged result abandoned by a crashed worker keeps the project lock until the job
  is reclaimed (a sweeper for orphaned staged jobs is Phase 8).
- Agent turns run one at a time per project; UI edits get 409 `project_busy` during a turn.
  Reply text is not streamed token by token (stage events are).
- The offline (`fake`) provider only understands the §23 / Scenario C phrases (desk, black legs, drawers, walnut,
  export, simple questions); anything else gets a clarifying reply.
- Every edit is a Blender job (≈5–20 s each, CPU thumbnail included); edits are disabled while one runs.
- Gizmo resize previews scale children, but the committed `set_dimensions` only resizes the object's
  own mesh, so children snap back when the new version loads.
- Restore moves the head pointer; undo/redo shortcuts and version compare are Phase 6.
- Project delete is a soft delete; file purge/retention job is Phase 8.
- Duplicate still copies project metadata only (not versions); copying the head version is Phase 6.
- Image tags are pinned; digest pinning and the offline bundle are Phase 8.
- shadcn/ui is approximated with small hand-written Tailwind components (`frontend/src/components/ui`)
  rather than the shadcn CLI, to avoid Radix/preflight conflicts with the prototype styles.

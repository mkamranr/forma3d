# Forma3D — Conversational AI 3D Studio

Describe an object in natural language; an LLM plans it, a validated modeling DSL drives
Blender (headless, in Docker), and the browser shows the GLB in a Three.js viewport. Refine it
through conversation with full version history.

Full specification: [`project-specification.md`](project-specification.md).

## Status

| Phase | Scope | State |
|---|---|---|
| 1 | Infrastructure, auth, project CRUD, dashboard, health checks | **Done** (pending first run on the Docker host) |
| 2 | Blender worker, modeling DSL v1, validator, GLB export | Next |
| 3 | Interactive workspace wired to real scenes | — |
| 4 | LLM integration (vLLM) — end-to-end vertical slice | — |
| 5–8 | LangGraph agents, conversational editing, advanced modeling, hardening | — |

## Repository layout

```
frontend/        React + TS + Vite (R3F viewport, TanStack Query, Zustand, Tailwind for new screens)
backend/         FastAPI, SQLAlchemy 2 (async), Alembic, JWT + rotating refresh cookies
agent-worker/    LangGraph agent worker (Phase 1: heartbeat stub)
blender-worker/  Blender job worker (Phase 1: heartbeat stub)
shared/          Shared Pydantic contracts (DSL, plans, jobs) — populated in Phase 2
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
.venv/Scripts/python -m pytest -q          # SQLite + fakeredis, no services needed
```

Frontend (Node 20):

```bash
cd frontend
npm ci
npm test           # Vitest
npm run build
VITE_API_PROXY=http://localhost:8000 npm run dev
```

## API (Phase 1)

| Method | Path | Notes |
|---|---|---|
| GET | `/health`, `/health/ready` | readiness checks PostgreSQL + Redis |
| POST | `/api/v1/auth/register` · `login` · `refresh` · `logout` | access token in body; refresh token in httpOnly SameSite=strict cookie |
| GET | `/api/v1/auth/me` | |
| GET/POST | `/api/v1/projects` | `?q=&status=active\|archived\|all&sort=&limit=&offset=` |
| GET/PUT/DELETE | `/api/v1/projects/{id}` | owner-scoped; other users get 404 |
| POST | `/api/v1/projects/{id}/duplicate` | |

Interactive docs at `/api/docs` when `APP_ENV != production`.

## Known limitations (Phase 1)

- The workspace still runs the prototype's **local demo scene** and simulated assistant
  (marked `DEMO`); Blender, the LLM and real versions arrive in Phases 2–4.
- Project delete is a soft delete; file purge/retention job is Phase 8.
- Duplicate copies metadata only until versioned storage exists (Phase 2).
- Image tags are pinned; digest pinning and the offline bundle are Phase 8.
- shadcn/ui is approximated with small hand-written Tailwind components (`frontend/src/components/ui`)
  rather than the shadcn CLI, to avoid Radix/preflight conflicts with the prototype styles.

# Contributing to forma3D

Thanks for your interest! Bug reports, ideas, documentation fixes and code are all welcome.
By taking part you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Ways to help

- **Report a bug** with the [bug report form](https://github.com/mkamranr/forma3d/issues/new/choose). Include the
  steps, what you expected, and logs. For assistant problems, the **How this was made** trace is very useful.
- **Suggest a feature** with the feature request form. Explain the problem it solves first.
- **Security issues** must not go in public issues. See [SECURITY.md](SECURITY.md).
- **Code and docs**: open an issue first for anything larger than a small fix, so we can agree on the approach.

## Development setup

You need Python 3.12 (3.10+ works for the tests), Node 20, and optionally Blender 4.5 LTS for the Blender
integration tests. Docker is not required for development.

```bash
git clone https://github.com/mkamranr/forma3d.git && cd forma3d

python -m venv backend/.venv
backend/.venv/bin/pip install -e shared -r backend/requirements-dev.txt \
  -r agent-worker/requirements-dev.txt -r blender-worker/requirements-dev.txt     # Windows: backend/.venv/Scripts/pip

# Whole stack without Docker: SQLite, in-process Redis, both workers, local Blender
BLENDER_BIN=/path/to/blender LLM_PROVIDER=fake backend/.venv/bin/python deployment/scripts/dev_local.py

cd frontend && npm ci && npm run dev          # http://localhost:5173 (proxies /api to :8000)
```

`LLM_PROVIDER=fake` uses the deterministic demo planner. To use a real model, set `LLM_PROVIDER=vllm`,
`LLM_BASE_URL` and `LLM_MODEL`.

## Running the checks

Run these before opening a pull request. CI runs the same checks.

```bash
cd shared && python -m pytest -q
cd backend && python -m pytest -q
cd agent-worker && python -m pytest -q
cd blender-worker && BLENDER_BIN=/path/to/blender python -m pytest -q   # Blender tests are skipped without it
ruff check backend agent-worker blender-worker shared
cd frontend && npm test && npm run build                                # Vitest + type check + build
```

## Project layout and rules of the road

| Area | Where | Notes |
|---|---|---|
| Modeling DSL and validator | `shared/forma3d_shared` | The trust boundary. A new operation needs its schema, validator rules, a `bl_runtime` handler (the registry test enforces this), tests and an entry in `docs/modeling-dsl.md` |
| API, database, versions | `backend/app` | Only the backend writes to the database. Schema changes need an Alembic migration in `backend/migrations/versions` |
| Agents and LangGraph workflow | `agent-worker/app` | Prompts live in `app/prompts/*.md`. Keep `tests/test_workflow.py` green with the fake provider |
| Blender execution | `blender-worker/app/bl_runtime` | Runs inside Blender: standard library and `bpy` only, no network, never `eval`/`exec` |
| UI | `frontend/src` | React + TypeScript. Keep the existing look; no CDN assets (the app must work air-gapped) |

General guidelines:
- Keep changes focused. One topic per pull request.
- Add or update tests for behaviour changes, and update the docs (`README.md`, `docs/`) when behaviour or
  configuration changes. New settings go in `.env.example` too.
- Python is formatted and linted with `ruff` (line length 120). TypeScript must pass `tsc` with no errors.
- Never let LLM output reach Blender or the shell other than as validated DSL.
- Never commit secrets, `.env` files or generated data (`data/`, `dist/`).

## Commit messages and pull requests

- Write commit messages in the imperative mood ("Add mirror modifier", not "Added…"). Use a short subject
  line, a blank line, then the why.
- Fill in the pull request template. Link the issue, describe how you tested, and add a screenshot for UI
  changes.
- A pull request is merged once CI passes and it has been reviewed.

## License

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE).

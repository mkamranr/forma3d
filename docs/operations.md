# Operating Forma3D

Commands below use `dc` for your compose invocation, for example:

```bash
alias dc='docker compose --env-file .env -f deployment/docker-compose.yml'   # + the overlays you use
```

## Health and monitoring

| Endpoint (inside the network) | Meaning |
|---|---|
| `GET /health` | process is alive (used by the container health check) |
| `GET /health/ready` | 503 if PostgreSQL or Redis is down; otherwise `ready` or `degraded` with `blender_workers`, `agent_workers`, `llm` (`ok` / `unavailable` / `unknown`) and `disk_free` |
| `GET /metrics` | Prometheus text; bearer `METRICS_TOKEN` when set. Blocked by the public nginx |

Key metrics: `forma3d_http_requests_total{method,route,status}`, `forma3d_http_request_duration_seconds`,
`forma3d_jobs{type,status}`, `forma3d_job_duration_seconds_avg_15m` / `_max_15m`,
`forma3d_queue_waiting{queue}`, `forma3d_queue_in_flight{queue}`, `forma3d_workers_alive{kind}`,
`forma3d_llm_reachable`, `forma3d_data_disk_bytes{kind}`, `forma3d_users`, `forma3d_projects{status}`.

`deployment/monitoring/alerts.yml` ships alerts for: backend down, no Blender / agent worker, LLM
unreachable, queue backlog, high failure rate, low disk.

Logs are JSON with `request_id` / `job_id`; follow one job across services with
`dc logs backend agent-worker blender-worker | grep <job_id>`.

## Backups

```bash
deployment/scripts/backup.sh /srv/backups          # KEEP=14 by default (older ones pruned)
```

Creates `forma3d-<UTC timestamp>/` with `database.dump` (pg_dump custom format, includes the LangGraph
checkpoints), `project_data.tar.gz` (versions, renders, exports, textures), `manifest.json` and
`SHA256SUMS`. It runs while the app is up: versions are immutable and the database is dumped after the
files, so every version row in the dump has its files. Schedule it, e.g. `crontab -e`:

```
15 2 * * * cd /opt/forma3d && deployment/scripts/backup.sh /srv/backups >> /var/log/forma3d-backup.log 2>&1
```

Copy backups off the host. They contain all user data; treat them like the database.

## Restore

```bash
deployment/scripts/restore.sh /srv/backups/forma3d-20261125-021500      # asks for confirmation; --yes to skip
```

Verifies checksums, stops the app services, replaces the data volume and recreates the database from the
dump, flushes Redis (queues and locks), runs migrations (so an older backup is upgraded) and starts
everything, waiting for `/health/ready`. Jobs that were running at backup time are failed by the sweeper.

Test restores regularly on a spare host: same commands, with a fresh clone and `.env`.

## Upgrades

```bash
deployment/scripts/backup.sh /srv/backups
git pull
dc up -d --build          # migrate runs before the backend starts
```

Offline: build a new bundle and run `offline-install.sh` in its folder with your `.env` copied in.
Rollback = restore the pre-upgrade backup with the previous release's code/images (migrations are not
downgraded automatically).

## Job recovery

The backend runs a maintenance loop every `MAINTENANCE_INTERVAL_SECONDS` (one leader across backend
replicas, via the Redis key `maintenance:leader`):

| Situation | Action |
|---|---|
| Blender worker crashed mid-job | another worker reclaims the stream entry after `RECLAIM_IDLE_SECONDS` (90 s) and re-runs it, but only if the original worker's heartbeat is gone; a live worker keeps its entry fresh |
| Job never picked up | failed after `QUEUED_JOB_TIMEOUT_SECONDS` ("No worker picked up this job") |
| Modeling / render / export stuck in `running` | failed after its timeout + `JOB_GRACE_SECONDS`, project lock released |
| Agent turn stuck | failed after `AGENT_JOB_TIMEOUT_SECONDS`, with a chat reply asking to retry |
| Staged result abandoned | discarded after `STAGED_JOB_TIMEOUT_SECONDS`, scene unchanged |
| Project lock with no active job | released |

Agent workers resume interrupted LangGraph runs from the PostgreSQL checkpoint. Staging directories
without a version row are cleaned on backend startup (`STAGING_ORPHAN_MINUTES`).

## Scaling

```bash
# .env
BLENDER_WORKERS=3
BLENDER_CPUS=2.0
BLENDER_MEMORY=4G
AGENT_WORKERS=2
dc up -d
```

Several Blender workers share one Redis consumer group; the per-project lock keeps builds for one
project serial, while different projects (and all renders/exports) run in parallel. Size
`BLENDER_TMP_SIZE` for your largest scene × 3. Agent workers are mostly waiting on the LLM; scale them
with the LLM server's concurrency. Watch `forma3d_queue_waiting` to decide.

## Configuration and administrators

Administrators change runtime settings under **avatar menu → Configuration**. Overrides are stored in the
`app_settings` table; each save bumps the Redis counter `config:version`, so every backend replica reloads within
about 3 s and every worker reloads before its next job. Reset a field to fall back to `.env`. To undo everything,
clear the table:

```bash
docker compose --env-file .env -f deployment/docker-compose.yml exec postgres   psql -U forma3d -d forma3d -c "DELETE FROM app_settings" && docker compose --env-file .env -f deployment/docker-compose.yml exec redis redis-cli INCR config:version
```

**Locked out?** Put your email in `ADMIN_EMAILS` and restart the backend: listed accounts are made
administrators at startup. If no administrator exists at all, the oldest active account is promoted.
`REGISTRATION_OPEN=false` closes sign-ups (the app setting wins once saved).

## Retention

| Data | Kept |
|---|---|
| Versions of active/archived projects | forever |
| Deleted projects (soft delete) | purged after `DELETED_PROJECT_RETENTION_DAYS` (files + rows) |
| Renders and exports | purged after `OUTPUT_RETENTION_DAYS`; the UI then offers to render/export again |

`0` disables purging.

## Rotating secrets

- `JWT_SECRET`: change it and restart the backend. Every user must log in again.
- `INTERNAL_SERVICE_TOKEN`: change it and restart backend + workers together (`dc up -d`).
- Database password: first `dc exec postgres psql -U forma3d -d forma3d -c "ALTER USER forma3d PASSWORD '<new>'"`,
  then update `.env` (`POSTGRES_PASSWORD` + `DATABASE_URL`) or `deployment/secrets/database_password`, then `dc up -d`.
- `generate-secrets.sh --force` regenerates everything; only use it on a fresh install.

## Troubleshooting

| Symptom | Check |
|---|---|
| UI says the assistant is unavailable | `/health/ready` → `llm`; `dc logs agent-worker`; the endpoint must be reachable from the container |
| Jobs stay queued | `forma3d_workers_alive{kind="blender"}`; `dc ps blender-worker`; `dc logs blender-worker` |
| Backend refuses to start: "production needs strong secrets" | run `generate-secrets.sh`, or set `APP_ENV=development` for tests only |
| `migrate` failed | `dc logs migrate`; the database password must match the initialised one |
| Renders are slow | add the GPU overlay or raise `BLENDER_CPUS`; draft quality for previews |
| Disk filling up | `forma3d_data_disk_bytes`; lower the retention days; prune old images with `docker image prune` |

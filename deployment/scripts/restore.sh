#!/usr/bin/env bash
# Restore a backup made by backup.sh. REPLACES the current database and project files.
#
#   deployment/scripts/restore.sh BACKUP_DIR [--yes]
#
# Steps: verify checksums -> stop app services -> restore the volume -> recreate the database from the dump
# -> run migrations (a backup from an older release is upgraded) -> start everything -> health check.
# Redis queues are flushed: jobs that were in flight at backup time are failed by the maintenance sweeper.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
SRC="${1:-}"
CONFIRM="${2:-}"
ENV_FILE="${ENV_FILE:-$ROOT/.env}"
COMPOSE="${COMPOSE:-docker compose}"
COMPOSE_FILES="${COMPOSE_FILES:-deployment/docker-compose.yml}"

[[ -n "$SRC" && -f "$SRC/SHA256SUMS" ]] || { echo "usage: $0 BACKUP_DIR [--yes]  (BACKUP_DIR must contain SHA256SUMS)" >&2; exit 2; }
SRC="$(cd "$SRC" && pwd)"

dc() {
  local args=(--env-file "$ENV_FILE")
  for f in $COMPOSE_FILES; do args+=(-f "$f"); done
  $COMPOSE "${args[@]}" "$@"
}
envval() { grep -E "^$1=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- || true; }
PG_USER="$(envval POSTGRES_USER)"; PG_USER="${PG_USER:-forma3d}"
PG_DB="$(envval POSTGRES_DB)"; PG_DB="${PG_DB:-forma3d}"
PROJECT="$(dc config --format json 2>/dev/null | python3 -c 'import json,sys;print(json.load(sys.stdin)["name"])' 2>/dev/null || echo forma3d)"
VOLUME="${PROJECT}_project_data"

echo "==> Verifying $SRC"
(cd "$SRC" && sha256sum -c SHA256SUMS)
grep -E '"(created_at|app_version|postgres)"' "$SRC/manifest.json" | sed 's/^ */    /'

if [[ "$CONFIRM" != "--yes" ]]; then
  read -r -p "This REPLACES all current data of '$PROJECT'. Type 'restore' to continue: " answer
  [[ "$answer" == "restore" ]] || { echo "aborted"; exit 1; }
fi

echo "==> Stopping application services"
dc stop frontend backend agent-worker blender-worker >/dev/null
dc up -d postgres redis >/dev/null
for _ in $(seq 1 60); do dc exec -T postgres pg_isready -U "$PG_USER" -d postgres >/dev/null 2>&1 && break; sleep 2; done

echo "--> project data volume ($VOLUME)"
docker volume create "$VOLUME" >/dev/null
docker run --rm --network none -v "$VOLUME:/data" -v "$SRC:/backup:ro" alpine:3.22 \
  sh -c 'find /data -mindepth 1 -delete && tar -xzf /backup/project_data.tar.gz -C /data && chown -R 10001:10001 /data 2>/dev/null || true'

echo "--> database ($PG_DB)"
dc exec -T postgres psql -v ON_ERROR_STOP=1 -U "$PG_USER" -d postgres \
  -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$PG_DB' AND pid <> pg_backend_pid();" \
  -c "DROP DATABASE IF EXISTS \"$PG_DB\";" -c "CREATE DATABASE \"$PG_DB\" OWNER \"$PG_USER\";" >/dev/null
dc exec -T postgres pg_restore -U "$PG_USER" -d "$PG_DB" --no-owner --no-privileges --exit-on-error < "$SRC/database.dump"

echo "--> flushing Redis queues/locks"
dc exec -T redis redis-cli FLUSHALL >/dev/null

echo "==> Migrating and starting"
dc run --rm migrate >/dev/null
dc up -d
for _ in $(seq 1 60); do
  if dc exec -T backend python -c "import urllib.request,sys;sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/health/ready').status!=200)" >/dev/null 2>&1; then
    echo "==> Restored and healthy."; exit 0
  fi
  sleep 3
done
echo "Stack started but the backend is not ready yet: check 'docker compose logs backend'." >&2
exit 1

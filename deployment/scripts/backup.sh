#!/usr/bin/env bash
# Back up a running Forma3D stack: PostgreSQL (pg_dump, custom format) + the project data volume.
#
#   deployment/scripts/backup.sh [BACKUP_ROOT]        # default: ./backups
#
# Produces BACKUP_ROOT/forma3d-YYYYmmdd-HHMMSS/ with
#   database.dump      pg_dump -Fc of the application database (includes the langgraph checkpoint schema)
#   project_data.tar.gz  every project's versions, renders, exports, uploaded textures
#   manifest.json      versions, sizes and SHA-256 checksums
#   SHA256SUMS         checksums in sha256sum format (verified by restore.sh)
#
# The backup is consistent enough to restore without the app stopped: versions are immutable and committed
# atomically (directory rename + one DB transaction), and the database dump is taken *after* the files,
# so every version row it contains has its files. Files without a row (in-flight staging) are cleaned up
# by the backend's reconciler after a restore. Redis is not backed up: it only holds queues, locks and
# event streams; jobs that were in flight are failed by the maintenance sweeper.
#
# Environment: COMPOSE (default "docker compose"), COMPOSE_FILES (default deployment/docker-compose.yml),
# ENV_FILE (default .env), KEEP (number of backups to keep, default 14; 0 = keep all).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
BACKUP_ROOT="${1:-$ROOT/backups}"
ENV_FILE="${ENV_FILE:-$ROOT/.env}"
COMPOSE="${COMPOSE:-docker compose}"
COMPOSE_FILES="${COMPOSE_FILES:-deployment/docker-compose.yml}"
KEEP="${KEEP:-14}"

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

stamp="$(date -u +%Y%m%d-%H%M%S)"
dest="$BACKUP_ROOT/forma3d-$stamp"
mkdir -p "$dest"
chmod 700 "$BACKUP_ROOT" "$dest"
echo "==> Backup to $dest"

docker volume inspect "$VOLUME" >/dev/null 2>&1 || { echo "volume $VOLUME not found (is the stack deployed?)" >&2; exit 1; }

echo "--> project data volume ($VOLUME)"
docker run --rm --network none \
  -v "$VOLUME:/data:ro" -v "$dest:/backup" \
  alpine:3.22 sh -c 'cd /data && tar -czf /backup/project_data.tar.gz --exclude="*/staging/*" --exclude="*.partial" .'

echo "--> PostgreSQL ($PG_DB)"
dc exec -T postgres pg_dump -U "$PG_USER" -d "$PG_DB" -Fc --no-owner --no-privileges > "$dest/database.dump"
[[ -s "$dest/database.dump" ]] || { echo "pg_dump produced an empty file" >&2; exit 1; }

sha() { sha256sum "$1" | cut -d' ' -f1; }
size() { stat -c %s "$1"; }
pg_version="$(dc exec -T postgres postgres --version | tr -d '\r')"
cat > "$dest/manifest.json" <<JSON
{
  "format": 1,
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "compose_project": "$PROJECT",
  "app_version": "$(envval FORMA3D_VERSION)",
  "git_commit": "$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)",
  "postgres": "$pg_version",
  "database": "$PG_DB",
  "files": {
    "database.dump": {"bytes": $(size "$dest/database.dump"), "sha256": "$(sha "$dest/database.dump")"},
    "project_data.tar.gz": {"bytes": $(size "$dest/project_data.tar.gz"), "sha256": "$(sha "$dest/project_data.tar.gz")"}
  }
}
JSON
(cd "$dest" && sha256sum database.dump project_data.tar.gz > SHA256SUMS)
chmod 600 "$dest"/*
echo "==> Done: $(du -sh "$dest" | cut -f1) in $dest"

if [[ "$KEEP" =~ ^[0-9]+$ && "$KEEP" -gt 0 ]]; then
  mapfile -t old < <(ls -1dt "$BACKUP_ROOT"/forma3d-* 2>/dev/null | tail -n +$((KEEP + 1)))
  for d in "${old[@]}"; do rm -rf -- "$d"; echo "    pruned $d"; done
fi
echo "Copy the backup off this host (it contains every user's data)."

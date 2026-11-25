#!/usr/bin/env bash
# Generate strong secrets for a Forma3D deployment.
#
#   deployment/scripts/generate-secrets.sh            # write them into .env (creates it from .env.example)
#   deployment/scripts/generate-secrets.sh --files    # write deployment/secrets/* for docker-compose.secrets.yml
#
# Existing values are never overwritten unless --force is given (rotating JWT_SECRET logs everyone out;
# rotating the database password needs `ALTER USER` in PostgreSQL first, see docs/operations.md).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MODE="env"
FORCE=0
for arg in "$@"; do
  case "$arg" in
    --files) MODE="files" ;;
    --force) FORCE=1 ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

rand() {  # URL-safe, no characters that need escaping in URLs or .env files
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -base64 48 | tr -d '\n/+=' | cut -c1-"$1"
  else
    head -c 96 /dev/urandom | base64 | tr -d '\n/+=' | cut -c1-"$1"
  fi
}

if [[ "$MODE" == "files" ]]; then
  dir="$ROOT/deployment/secrets"
  mkdir -p "$dir"
  chmod 700 "$dir"
  for name in jwt_secret:64 internal_service_token:48 database_password:32; do
    file="$dir/${name%%:*}"
    if [[ -s "$file" && $FORCE -eq 0 ]]; then
      echo "keep    $file"
    else
      (umask 077; rand "${name##*:}" > "$file")
      echo "created $file"
    fi
  done
  # The LLM key is yours to provide (empty file = no key).
  [[ -e "$dir/llm_api_key" ]] || { (umask 077; : > "$dir/llm_api_key"); echo "created $dir/llm_api_key (empty: put your LLM API key here if the endpoint needs one)"; }
  # Compose bind-mounts secrets; the containers run as non-root users and must be able to read them.
  chmod 644 "$dir"/*
  echo "Leave JWT_SECRET, INTERNAL_SERVICE_TOKEN and POSTGRES_PASSWORD empty in .env and remove the password"
  echo "from DATABASE_URL (postgresql+asyncpg://forma3d@postgres:5432/forma3d)."
  exit 0
fi

env_file="$ROOT/.env"
if [[ ! -f "$env_file" ]]; then
  cp "$ROOT/.env.example" "$env_file"
  echo "created $env_file from .env.example"
fi
chmod 600 "$env_file"

current() { grep -E "^$1=" "$env_file" | head -1 | cut -d= -f2- || true; }
set_var() {  # set_var NAME VALUE
  local tmp
  tmp="$(mktemp)"
  if grep -qE "^$1=" "$env_file"; then
    awk -v k="$1" -v v="$2" 'BEGIN{FS=OFS="="} $1==k {print k "=" v; next} {print}' "$env_file" > "$tmp"
  else
    cp "$env_file" "$tmp"; echo "$1=$2" >> "$tmp"
  fi
  cat "$tmp" > "$env_file"; rm -f "$tmp"
}
weak() { [[ -z "$1" || "$1" == *change-me* || ${#1} -lt 24 ]]; }

for spec in JWT_SECRET:64 INTERNAL_SERVICE_TOKEN:48; do
  name="${spec%%:*}"
  if weak "$(current "$name")" || [[ $FORCE -eq 1 ]]; then
    set_var "$name" "$(rand "${spec##*:}")"; echo "set     $name"
  else
    echo "keep    $name"
  fi
done

pw="$(current POSTGRES_PASSWORD)"
if [[ -z "$pw" || "$pw" == "change-me" || $FORCE -eq 1 ]]; then
  pw="$(rand 32)"
  set_var POSTGRES_PASSWORD "$pw"
  user="$(current POSTGRES_USER)"; db="$(current POSTGRES_DB)"
  set_var DATABASE_URL "postgresql+asyncpg://${user:-forma3d}:${pw}@postgres:5432/${db:-forma3d}"
  echo "set     POSTGRES_PASSWORD (+ DATABASE_URL)"
else
  echo "keep    POSTGRES_PASSWORD"
fi
echo "Done. Review $env_file (LLM_BASE_URL, LLM_MODEL, COOKIE_SECURE) before starting the stack."

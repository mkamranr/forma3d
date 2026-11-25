#!/usr/bin/env bash
# Install or upgrade Forma3D from an offline bundle (see offline-bundle.sh). Run inside the extracted folder:
#
#   tar -xf forma3d-VERSION.tar && cd forma3d-VERSION
#   deployment/scripts/offline-install.sh            # verify, load images, migrate, start
#
# First install: creates .env with generated secrets, then stops so you can set LLM_BASE_URL etc.
# Upgrade: copy your existing .env into this folder first (FORMA3D_VERSION is updated for you); take a
# backup before (deployment/scripts/backup.sh) — migrations run automatically.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
VERSION="$(cat VERSION)"

echo "==> Verifying bundle checksums"
sha256sum --quiet -c SHA256SUMS

echo "==> Loading images"
docker load -i images.tar

echo "==> Verifying image digests"
while IFS=$'\t' read -r img id _; do
  actual="$(docker image inspect --format '{{.Id}}' "$img" 2>/dev/null || true)"
  [[ "$actual" == "$id" ]] || { echo "image $img: expected $id, got ${actual:-missing}" >&2; exit 1; }
done < images.lock
echo "    all $(wc -l < images.lock) images match images.lock"

if [[ ! -f .env ]]; then
  deployment/scripts/generate-secrets.sh
  sed -i "s/^FORMA3D_VERSION=.*/FORMA3D_VERSION=$VERSION/" .env
  echo
  echo "Created .env. Set LLM_BASE_URL / LLM_MODEL (an endpoint reachable inside this network) and"
  echo "COOKIE_SECURE, then run this script again."
  exit 0
fi
if grep -q '^FORMA3D_VERSION=' .env; then
  sed -i "s/^FORMA3D_VERSION=.*/FORMA3D_VERSION=$VERSION/" .env
else
  echo "FORMA3D_VERSION=$VERSION" >> .env
fi

files=(-f deployment/docker-compose.yml)
[[ -f deployment/secrets/jwt_secret ]] && files+=(-f deployment/docker-compose.secrets.yml)
dc() { docker compose --env-file .env "${files[@]}" "$@"; }

echo "==> Migrating and starting $VERSION"
dc up -d --no-build postgres redis
dc run --rm --no-deps migrate
dc up -d --no-build
dc ps
echo "Open http://<this-host>:$(grep -E '^FRONTEND_PORT=' .env | cut -d= -f2 || echo 3000)"

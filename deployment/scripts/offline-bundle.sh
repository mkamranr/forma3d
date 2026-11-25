#!/usr/bin/env bash
# Build an offline (air-gapped) install bundle on a machine WITH internet access.
#
#   deployment/scripts/offline-bundle.sh [VERSION] [--with-monitoring]
#
# Produces dist/forma3d-VERSION/ (+ .tar) containing
#   images.tar          every image the stack needs (docker save)
#   images.lock         image -> content digest, verified by offline-install.sh
#   deployment/, .env.example, docs/   compose files, overlays, scripts and docs
#   SHA256SUMS
# The target host needs only Docker Engine + the compose plugin; nothing is pulled at install or run time
# (the LLM endpoint is configured to a server inside the network).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
VERSION="$(git describe --tags --always 2>/dev/null || date -u +%Y%m%d)"
MONITORING=0
for arg in "$@"; do
  case "$arg" in
    --with-monitoring) MONITORING=1 ;;
    -*) echo "unknown option: $arg" >&2; exit 2 ;;
    *) VERSION="$arg" ;;
  esac
done
OUT="$ROOT/dist/forma3d-$VERSION"
rm -rf "$OUT"; mkdir -p "$OUT"

export FORMA3D_VERSION="$VERSION"
# A throwaway env file: compose needs one to resolve the file, the values are irrelevant for building.
tmp_env="$(mktemp)"; trap 'rm -f "$tmp_env"' EXIT
cp .env.example "$tmp_env"
sed -i "s/^FORMA3D_VERSION=.*/FORMA3D_VERSION=$VERSION/" "$tmp_env"

files=(-f deployment/docker-compose.yml)
[[ $MONITORING -eq 1 ]] && files+=(-f deployment/docker-compose.monitoring.yml)

echo "==> Building application images ($VERSION)"
docker compose --env-file "$tmp_env" "${files[@]}" build --pull
echo "==> Pulling third-party images"
docker compose --env-file "$tmp_env" "${files[@]}" pull --ignore-buildable postgres redis $([[ $MONITORING -eq 1 ]] && echo prometheus)

mapfile -t images < <(docker compose --env-file "$tmp_env" "${files[@]}" config --images | sort -u)
images+=("alpine:3.22")   # used by backup.sh / restore.sh
docker pull -q alpine:3.22 >/dev/null

echo "==> Recording digests"
: > "$OUT/images.lock"
for img in "${images[@]}"; do
  id="$(docker image inspect --format '{{.Id}}' "$img")"
  repo="$(docker image inspect --format '{{join .RepoDigests ","}}' "$img")"
  printf '%s\t%s\t%s\n' "$img" "$id" "${repo:--}" >> "$OUT/images.lock"
  echo "    $img  $id"
done

echo "==> Saving images (this takes a while; the Blender image is the largest)"
docker save "${images[@]}" -o "$OUT/images.tar"

echo "==> Copying deployment files"
mkdir -p "$OUT/deployment"
cp -r deployment/docker-compose*.yml deployment/monitoring deployment/scripts "$OUT/deployment/"
rm -f "$OUT/deployment/monitoring/metrics_token" "$OUT/deployment/scripts/dev_local.py"
cp .env.example "$OUT/.env.example"
sed -i "s/^FORMA3D_VERSION=.*/FORMA3D_VERSION=$VERSION/" "$OUT/.env.example"
mkdir -p "$OUT/docs"; cp docs/deployment.md docs/operations.md "$OUT/docs/" 2>/dev/null || true
# Compose files reference build contexts; offline the images already exist, so builds are never triggered
# (`docker compose up --no-build`). Empty placeholders keep `config` happy.
mkdir -p "$OUT/frontend" "$OUT/backend" "$OUT/agent-worker" "$OUT/blender-worker"
echo "$VERSION" > "$OUT/VERSION"

(cd "$OUT" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS)
echo "==> Packing"
tar -C "$ROOT/dist" -cf "$ROOT/dist/forma3d-$VERSION.tar" "forma3d-$VERSION"
echo "Bundle: dist/forma3d-$VERSION.tar ($(du -sh "$ROOT/dist/forma3d-$VERSION.tar" | cut -f1))"
echo "Copy it to the target host and run: deployment/scripts/offline-install.sh (from inside the extracted folder)"

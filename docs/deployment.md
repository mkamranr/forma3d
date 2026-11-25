# Deploying Forma3D with Docker

This guide installs the full stack on one Linux Docker host. Day-2 tasks (backups, upgrades, monitoring,
scaling, recovery) are in [operations.md](operations.md).

## 1. Requirements

| | Minimum | Recommended |
|---|---|---|
| OS | Linux x86_64 (Ubuntu 22.04+/Debian 12/RHEL 9) | same |
| Docker | Engine 24+ with the compose plugin v2.20+ | latest stable |
| CPU / RAM | 4 cores / 8 GB | 8+ cores / 16 GB (more Blender workers) |
| Disk | 20 GB (images ≈ 3 GB + data) | SSD, sized for versions + renders |
| GPU | not needed | NVIDIA + Container Toolkit for faster renders |
| LLM | an OpenAI-compatible endpoint (vLLM recommended) reachable from the host | GPU server on the same network |

Only one port is published (the web UI, default `3000`). PostgreSQL, Redis, the backend and the
workers sit on internal Docker networks; only the agent worker can reach the outside (the LLM).

## 2. Get the code

```bash
git clone https://github.com/mkamranr/forma3d.git
cd forma3d
```

Working from another machine? Point your Docker CLI at the host instead of copying files:

```bash
docker context create forma3d-remote --docker "host=ssh://user@docker-host"
docker context use forma3d-remote
```

## 3. Configure

```bash
deployment/scripts/generate-secrets.sh     # creates .env from .env.example with strong random secrets
```

Then edit `.env`:

| Variable | Set to |
|---|---|
| `LLM_BASE_URL` | your endpoint, e.g. `http://10.0.0.5:8000/v1` (must be reachable from containers) |
| `LLM_MODEL` | the served model name (`vllm serve <name>`) |
| `LLM_THINKING` | `off` for reasoning models (much faster plans) |
| `LLM_API_KEY` | the key if your endpoint needs one |
| `COOKIE_SECURE` | `true` when users reach the site over HTTPS |
| `FRONTEND_PORT` / `FRONTEND_BIND` | public port; `127.0.0.1` when a TLS proxy runs on the same host |
| `BLENDER_WORKERS`, `BLENDER_CPUS`, `BLENDER_MEMORY` | worker count and per-worker limits |

`APP_ENV=production` refuses to start with default or short secrets and hides the API docs.

No LLM yet? `LLM_PROVIDER=fake` runs a deterministic offline planner (desk, cabinet, chair demos).

## 4. Start

```bash
docker compose --env-file .env -f deployment/docker-compose.yml up -d --build
docker compose --env-file .env -f deployment/docker-compose.yml ps
```

The first build downloads Blender 4.5 LTS (checksum-verified) and takes a few minutes. Startup order is
enforced with health checks: postgres + redis → `migrate` (`alembic upgrade head`, one-shot) →
backend → workers → frontend.

Open `http://<docker-host>:3000`, register an account and create a project.

Check health:

```bash
docker compose --env-file .env -f deployment/docker-compose.yml exec backend \
  python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/health/ready').read().decode())"
```

`"status": "ready"` means database, Redis, at least one Blender and one agent worker and the LLM are all up;
`degraded` names what is missing.

`make up`, `make ps`, `make logs` wrap the same commands.

## 5. Optional overlays

Add them with extra `-f` flags (keep the same set on every command, e.g. in a shell alias):

```bash
alias f3d='docker compose --env-file .env -f deployment/docker-compose.yml -f deployment/docker-compose.secrets.yml -f deployment/docker-compose.gpu.yml'
f3d up -d
```

### Secrets as files (recommended)

```bash
deployment/scripts/generate-secrets.sh --files   # writes deployment/secrets/{jwt_secret,internal_service_token,database_password,llm_api_key}
```

Empty `JWT_SECRET`, `INTERNAL_SERVICE_TOKEN` and `POSTGRES_PASSWORD` in `.env`, remove the password from
`DATABASE_URL` (`postgresql+asyncpg://forma3d@postgres:5432/forma3d`), then start with
`-f deployment/docker-compose.secrets.yml`. Secrets are mounted at `/run/secrets/*` and never appear in
`docker inspect`. Switching an existing install: the database password must match the one PostgreSQL was
initialised with (see operations.md, "Rotating secrets").

### NVIDIA GPU rendering

Install the NVIDIA driver and the NVIDIA Container Toolkit, then add `-f deployment/docker-compose.gpu.yml`.
`F3D_CYCLES_DEVICE` is `OPTIX` (RTX) or `CUDA`; with no usable device Cycles falls back to the CPU.

### Prometheus

```bash
grep '^METRICS_TOKEN=' .env | cut -d= -f2- | tr -d '\n' > deployment/monitoring/metrics_token
docker compose ... -f deployment/docker-compose.monitoring.yml up -d
```

Prometheus listens on `127.0.0.1:9090` with the alert rules from `deployment/monitoring/alerts.yml`.

## 6. HTTPS

Put a TLS-terminating reverse proxy (Caddy, Traefik, nginx) in front of the frontend port, set
`FRONTEND_BIND=127.0.0.1` and `COOKIE_SECURE=true`. Server-sent events need buffering off; for nginx:

```nginx
location / {
    proxy_pass http://127.0.0.1:3000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
    proxy_http_version 1.1;
    proxy_buffering off;
    proxy_read_timeout 1h;
    client_max_body_size 12m;
}
```

Once HTTPS works, uncomment the HSTS header in `frontend/security-headers.inc` and rebuild the frontend.

## 7. Offline / air-gapped install

On a machine with internet access:

```bash
deployment/scripts/offline-bundle.sh 1.0.0          # [--with-monitoring]
# -> dist/forma3d-1.0.0.tar (images + images.lock with digests + compose files + SHA256SUMS)
```

On the target host (Docker only, no internet):

```bash
tar -xf forma3d-1.0.0.tar && cd forma3d-1.0.0
deployment/scripts/offline-install.sh      # 1st run: verifies, loads images, creates .env -> edit LLM_* settings
deployment/scripts/offline-install.sh      # 2nd run: migrates and starts
```

The installer verifies the bundle checksums and every loaded image ID against `images.lock` before
anything runs. The browser loads no external fonts, scripts or CDNs.

## 8. Hardening built in

- Every application container: read-only root filesystem, `no-new-privileges`, all capabilities
  dropped, non-root user (uid 10001), PID limit, CPU/memory limits, tmpfs for scratch space.
- Networks: `edge` (published UI only), `app` and `data` internal-only, `llm` egress only for the agent.
- nginx: security headers (CSP, frame and referrer policy, COOP), `/internal/` and `/metrics` blocked,
  12 MB body limit, gzip, immutable caching for hashed assets.
- Blender runs headless with a minimal environment (no tokens or database URLs), per-job timeouts and a
  size-bounded workspace; it only ever executes validated DSL operations, never generated code.
- Logs: JSON, size-rotated (`20m × 5` per container), with request/job correlation IDs.

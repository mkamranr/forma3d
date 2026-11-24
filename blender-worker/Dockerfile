# Build context: repository root.
# Stage 1: fetch the official Blender LTS build and verify its checksum (fails the build on mismatch).
FROM debian:bookworm-slim AS blender
ARG BLENDER_VERSION=4.5.17
ARG BLENDER_SERIES=4.5
ARG BLENDER_SHA256=b7d09d1aa7c346fed1e56390e815dbf1589d47324734fbad9e2007fe75ca0b8d
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl xz-utils \
 && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL -o /tmp/blender.tar.xz \
      "https://download.blender.org/release/Blender${BLENDER_SERIES}/blender-${BLENDER_VERSION}-linux-x64.tar.xz" \
 && echo "${BLENDER_SHA256}  /tmp/blender.tar.xz" | sha256sum -c - \
 && mkdir -p /opt/blender \
 && tar -xJf /tmp/blender.tar.xz -C /opt/blender --strip-components=1 \
 && rm /tmp/blender.tar.xz

# Stage 2: worker service (Python 3.12) + headless Blender runtime libraries.
FROM python:3.12.16-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    BLENDER_BIN=/opt/blender/blender WORK_ROOT=/tmp/forma3d-jobs HOME=/tmp
RUN apt-get update && apt-get install -y --no-install-recommends \
      libx11-6 libxi6 libxxf86vm1 libxfixes3 libxrender1 libxkbcommon0 libsm6 libice6 libgl1 libegl1 \
      libglu1-mesa libxext6 libxinerama1 libxcursor1 libxrandr2 \
 && rm -rf /var/lib/apt/lists/*
COPY --from=blender /opt/blender /opt/blender
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --home-dir /app app \
 && mkdir -p /data /tmp/forma3d-jobs && chown app:app /data /tmp/forma3d-jobs
WORKDIR /app
COPY blender-worker/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt
COPY shared/ /opt/forma3d-shared/
RUN pip install /opt/forma3d-shared
COPY deployment/scripts/healthcheck_liveness.py /usr/local/bin/healthcheck_liveness.py
# Smoke check at build time: Blender starts headless.
RUN /opt/blender/blender --background --factory-startup --python-expr "import bpy; print(bpy.app.version_string)" \
    | grep -q "4.5"

FROM base AS dev
COPY blender-worker/requirements-dev.txt ./requirements-dev.txt
RUN pip install -r requirements-dev.txt
COPY blender-worker/ ./
USER app
CMD ["python", "-m", "app.worker"]

FROM base AS runtime
COPY blender-worker/app ./app
USER app
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD python /usr/local/bin/healthcheck_liveness.py
CMD ["python", "-m", "app.worker"]

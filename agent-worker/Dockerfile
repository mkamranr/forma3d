# Build context: repository root.
FROM python:3.12.16-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --home-dir /app app
WORKDIR /app
COPY agent-worker/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt
COPY shared/ /opt/forma3d-shared/
RUN pip install /opt/forma3d-shared
COPY deployment/scripts/healthcheck_liveness.py /usr/local/bin/healthcheck_liveness.py

FROM base AS dev
COPY agent-worker/requirements-dev.txt ./requirements-dev.txt
RUN pip install -r requirements-dev.txt
COPY agent-worker/ ./
USER app
CMD ["python", "-m", "app.main"]

FROM base AS runtime
COPY agent-worker/app ./app
USER app
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD python /usr/local/bin/healthcheck_liveness.py
CMD ["python", "-m", "app.main"]

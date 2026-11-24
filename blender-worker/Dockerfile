# Build context: repository root.
FROM python:3.12.16-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --home-dir /app app \
 && mkdir -p /data && chown app:app /data
WORKDIR /app
COPY blender-worker/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt
COPY blender-worker/app ./app
COPY deployment/scripts/healthcheck_liveness.py /usr/local/bin/healthcheck_liveness.py
USER app
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD python /usr/local/bin/healthcheck_liveness.py
CMD ["python", "-m", "app.worker"]

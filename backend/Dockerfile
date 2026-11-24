# Build context: repository root (so the shared package can be installed in later phases).
FROM python:3.12.16-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --home-dir /app app \
 && mkdir -p /data && chown app:app /data
WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

FROM base AS dev
COPY backend/requirements-dev.txt ./requirements-dev.txt
RUN pip install -r requirements-dev.txt
COPY backend/ ./
USER app
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

FROM base AS runtime
COPY backend/ ./
USER app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]

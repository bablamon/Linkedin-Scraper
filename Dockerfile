# syntax=docker/dockerfile:1
# Single-stage-built venv copied into a slim runtime. No compose: one image,
# one process, deployable straight from Coolify.

FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
COPY requirements.txt .
# curl_cffi ships manylinux wheels with libcurl-impersonate bundled, and
# selectolax ships wheels too, so no compiler is needed here.
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install -r requirements.txt


FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    PORT=8000 \
    PROXY_FILE=/data/proxies.txt

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY app ./app

# Mount point for Coolify persistent storage. Created (and owned) up front so
# the container still starts when no volume is attached.
RUN mkdir -p /data \
    && useradd --system --create-home --uid 10001 scraper \
    && chown -R scraper:scraper /app /data
USER scraper

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,os,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/health', timeout=4).status==200 else 1)"

# Exactly one worker, deliberately. The rate limiter, the outbound pacer and the
# cache are all in-process: a second worker would double the real egress rate
# against LinkedIn while still reporting "10/min" per worker.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --no-access-log"]

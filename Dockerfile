# syntax=docker/dockerfile:1
# Chromium's system dependencies are the classic "works locally, breaks on the
# VPS" failure. Rather than hand-maintaining an apt list (or guessing a Microsoft
# base-image tag), `playwright install --with-deps` installs exactly the packages
# its own pinned Chromium build needs. One command, correct by construction.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000 \
    PROXY_FILE=/data/proxies.txt \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Chromium + its OS dependencies. Needs root, so it runs before the user switch.
RUN python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

COPY app ./app

# Coolify persistent-storage mount, created up front so the container still
# starts with no volume attached. The browser cache dir must be writable by the
# unprivileged user that actually runs Chromium.
RUN mkdir -p /data \
    && useradd --system --create-home --uid 10001 scraper \
    && chown -R scraper:scraper /app /data /opt/playwright
USER scraper

EXPOSE 8000

# Longer start-period than the HTTP build: Chromium launches during startup.
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request,os,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/health', timeout=8).status==200 else 1)"

# Exactly one worker, deliberately. The rate limiter, the outbound pacer, the
# cache and the single Chromium instance are all in-process — a second worker
# would double both the real egress rate and the memory footprint.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --no-access-log"]

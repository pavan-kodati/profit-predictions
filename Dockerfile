# Small production image: Python 3.12 slim, non-root user, no build tools left behind.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Kolkata

WORKDIR /app
RUN useradd --create-home --uid 10001 app

COPY pyproject.toml ./
COPY src ./src
RUN pip install . && rm -rf /root/.cache

RUN mkdir -p /app/data && chown -R app:app /app
USER app

ENV DATABASE_URL=sqlite:////app/data/app.db \
    DATA_DIR=/app/data

EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

# Migrate on start, then serve. Two workers fit comfortably in 1 GB RAM.
CMD ["sh", "-c", "stockresearch init-db && uvicorn stockresearch.web.app:app_factory --factory --host 0.0.0.0 --port 8000 --workers 2 --proxy-headers --forwarded-allow-ips='*'"]

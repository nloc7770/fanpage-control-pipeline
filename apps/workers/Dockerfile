# CPU worker image -- runs the download / qwen / render-prep queues.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# System packages: ffmpeg (for render-prep helpers) + build tools for asyncpg.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        build-essential \
        libpq-dev \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install our internal packages in dependency order.
COPY packages/shared-py /app/packages/shared-py
COPY packages/database /app/packages/database
COPY packages/queue /app/packages/queue
COPY packages/storage /app/packages/storage
COPY packages/ai /app/packages/ai
COPY packages/ffmpeg /app/packages/ffmpeg
RUN pip install \
    /app/packages/shared-py \
    /app/packages/database \
    /app/packages/queue \
    /app/packages/storage \
    /app/packages/ai \
    /app/packages/ffmpeg

# Workers code + remaining python deps.
COPY apps/workers /app/apps/workers
COPY services /app/services
RUN pip install \
    "celery>=5.3" \
    "redis>=5.0" \
    "httpx>=0.27" \
    "loguru>=0.7" \
    "yt-dlp>=2024.3" \
    "anyio>=4.3"

ENV PYTHONPATH=/app:/app/packages/shared-py:/app/packages/database:/app/packages/queue:/app/packages/storage:/app/packages/ai:/app/packages/ffmpeg:/app/services

# Default to the CPU queues; the GPU image overrides this.
CMD ["celery", "-A", "apps.workers.worker_app", "worker", \
     "-Q", "download,qwen,render-prep", "-l", "INFO", "--concurrency=4"]

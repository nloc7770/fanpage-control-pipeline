# apps/api

FastAPI service for the Shortform Factory monorepo. Owns the REST surface and
SSE multiplexer described in `docs/api-contracts.md` and `ARCHITECTURE.md`.

## Layout

```
apps/api/
  pyproject.toml          # fastapi, uvicorn, sqlalchemy[asyncio], asyncpg, redis>=5, celery, loguru
  Dockerfile              # multi-stage python:3.11-slim
  alembic.ini             # re-uses packages/database/migrations
  app/
    main.py               # FastAPI() factory + lifespan
    config.py             # pydantic-settings Settings
    deps.py               # DI providers
    celery_client.py      # Celery("factory") with task_routes
    logging_setup.py      # loguru JSON sink + uvicorn intercept
    sse.py                # SSE framing + keep-alive
    errors.py             # AppError + exception handlers
    routers/{health,jobs,assets}.py
    services/{job_service,event_bus}.py
  tests/
    conftest.py           # aiosqlite + fakeredis + monkeypatched celery
    test_health.py
    test_jobs_create.py
    test_jobs_list.py
    test_sse.py
```

## Local development

Install in editable mode from the monorepo root so the first-party packages
resolve:

```bash
pip install -e packages/shared-py
pip install -e packages/database
pip install -e 'apps/api[dev]'
```

Copy `.env.example` to `.env` (at repo root) and adjust `DATABASE_URL` /
`REDIS_URL` for your environment. Pydantic settings will pick `.env` up from
the working directory.

Run the dev server:

```bash
cd apps/api
uvicorn app.main:app --reload --host 0.0.0.0 --port 8080
```

Run migrations against your Postgres (requires `packages/database`):

```bash
alembic -c apps/api/alembic.ini upgrade head
```

Optional symlink for convenience (so `alembic` finds the script dir
without `-c`):

```bash
ln -s ../../packages/database/migrations apps/api/migrations
```

## Tests

```bash
cd apps/api
pytest -q
```

The suite uses `aiosqlite` for the DB and `fakeredis.aioredis` for Redis, so it
needs no running infrastructure. Celery dispatch is monkeypatched in
`conftest.py`.

## Docker

```bash
docker build -t shortform-api -f apps/api/Dockerfile .
docker run --rm -p 8080:8080 \
    -e DATABASE_URL=... -e REDIS_URL=... shortform-api
```

## Endpoint summary

| Method | Path                          | Description                          |
|--------|-------------------------------|--------------------------------------|
| GET    | `/healthz`                    | Liveness (always 200)                |
| GET    | `/readyz`                     | Readiness: DB + Redis ping           |
| POST   | `/jobs`                       | Create job + dispatch download       |
| GET    | `/jobs`                       | Paginated list                       |
| GET    | `/jobs/{id}`                  | Job detail                           |
| GET    | `/jobs/{id}/clips`            | Clip list for a job                  |
| GET    | `/jobs/{id}/events`           | SSE stream (Redis pub/sub + replay)  |
| GET    | `/assets/{id}`                | Asset metadata                       |
| GET    | `/assets/{id}/download`       | Streaming download (local storage)   |

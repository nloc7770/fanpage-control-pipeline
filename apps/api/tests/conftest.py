"""Test fixtures.

Strategy: spin up an in-memory async SQLite with the JSONB/UUID/Enum types from
`database.models` rebound to portable types via SQLAlchemy's
`TypeDecorator` machinery (PG_UUID has built-in fallback to CHAR(32),
`JSONB` is mapped to `JSON`, and native enums become CHECK constraints).
We monkeypatch the ASSET/JOB/CLIP_STAGE enum columns to `native_enum=False`
before the metadata is materialized so SQLite is happy.

Celery dispatch is monkeypatched to a recording stub. Redis comes from
`fakeredis.aioredis`.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import fakeredis.aioredis
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import JSON, Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.dialects.sqlite import dialect as sqlite_dialect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

# Force native_enum=False on the ORM Enum columns BEFORE metadata is bound to a
# dialect. Must happen before importing the FastAPI app.
from database import models as db_models

for col in (
    db_models.Job.__table__.c.status,
    db_models.Clip.__table__.c.status,
    db_models.RenderTask.__table__.c.status,
    db_models.Asset.__table__.c.kind,
):
    t = col.type
    if isinstance(t, SAEnum):
        t.native_enum = False  # type: ignore[attr-defined]
        t.name = None  # type: ignore[attr-defined]


# Render JSONB as plain JSON on SQLite.
@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # type: ignore[no-untyped-def]
    return compiler.visit_JSON(JSON(), **kw)


# Render PG_UUID as CHAR(36) on SQLite. The dialect already coerces UUID values
# to/from text via TypeDecorator-style behaviour when native_uuid=False, but the
# DDL compiler still emits "UUID" -- override it explicitly.
@compiles(PG_UUID, "sqlite")
def _compile_pg_uuid_sqlite(element, compiler, **kw):  # type: ignore[no-untyped-def]
    return "CHAR(36)"


# Replace server-side defaults that don't exist on SQLite (gen_random_uuid()
# and the various now() function calls) with Python-side defaults.
def _normalize_server_defaults_for_sqlite() -> None:
    from datetime import datetime, timezone
    from uuid import uuid4

    from sqlalchemy import ColumnDefault
    from sqlalchemy.sql import functions as sa_func

    def _now() -> datetime:
        return datetime.now(timezone.utc)

    for table in db_models.Base.metadata.tables.values():
        for column in table.columns:
            sd = column.server_default
            if sd is None:
                continue
            arg = getattr(sd, "arg", None)
            if not isinstance(arg, sa_func.Function):
                continue
            fname = arg.name
            column.server_default = None
            if fname == "gen_random_uuid" and column.default is None:
                column.default = ColumnDefault(uuid4)
            elif fname == "now":
                if column.default is None:
                    column.default = ColumnDefault(_now)
                if column.onupdate is None and column.name == "updated_at":
                    column.onupdate = ColumnDefault(_now, for_update=True)


_normalize_server_defaults_for_sqlite()

from database.base import Base  # noqa: E402

from app import celery_client  # noqa: E402
from app.main import create_app  # noqa: E402


@pytest.fixture(scope="session")
def event_loop() -> asyncio.AbstractEventLoop:
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture()
async def engine_and_factory() -> AsyncIterator[tuple[Any, async_sessionmaker[AsyncSession]]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield engine, factory
    finally:
        await engine.dispose()


@pytest.fixture()
def fake_redis() -> fakeredis.aioredis.FakeRedis:
    return fakeredis.aioredis.FakeRedis(decode_responses=False)


@pytest.fixture()
def celery_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Records every `celery_client.dispatch` invocation."""
    calls: list[dict[str, Any]] = []

    def _stub(name: str, args: list[Any], queue: str) -> str:
        calls.append({"name": name, "args": list(args), "queue": queue})
        return "task-id-stub"

    monkeypatch.setattr(celery_client, "dispatch", _stub)
    return calls


@pytest_asyncio.fixture()
async def app(
    engine_and_factory: tuple[Any, async_sessionmaker[AsyncSession]],
    fake_redis: fakeredis.aioredis.FakeRedis,
):
    engine, factory = engine_and_factory
    app = create_app()
    app.state.engine = engine
    app.state.session_factory = factory
    app.state.redis = fake_redis
    return app


@pytest_asyncio.fixture()
async def client(app) -> AsyncIterator[AsyncClient]:  # type: ignore[no-untyped-def]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Trigger lifespan manually so app.state is preserved.
        async with app.router.lifespan_context(app):
            yield ac

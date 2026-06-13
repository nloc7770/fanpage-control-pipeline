"""Pytest fixtures: every MOCK_* flag ON, fakeredis, in-memory sqlite."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

import fakeredis
import pytest
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# --------------------------------------------------------------------------
# Make sibling packages importable without `pip install -e` in CI.
# --------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[3]
for sub in (
    "",
    "packages/shared-py",
    "packages/database",
    "packages/queue",
    "packages/storage",
    "packages/ai",
    "packages/ffmpeg",
    "services",
):
    p = str(REPO_ROOT / sub) if sub else str(REPO_ROOT)
    if p not in sys.path:
        sys.path.insert(0, p)


# --------------------------------------------------------------------------
# Env: all mocks on; storage to a tmp dir; redis url that fakeredis intercepts.
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True, scope="session")
def _env_setup() -> None:
    os.environ.update(
        {
            "MOCK_DOWNLOAD": "1",
            "MOCK_ASR": "1",
            "MOCK_LLM": "1",
            "MOCK_YOLO": "1",
            "MOCK_RENDER": "1",
            "ENABLE_DIARIZATION": "1",
            "STORAGE_BACKEND": "local",
            "REDIS_URL": "redis://localhost:6379/0",
            "CELERY_BROKER_URL": "memory://",
            "CELERY_RESULT_BACKEND": "cache+memory://",
            "QWEN_BASE_URL": "http://fake.invalid/v1",
            "QWEN_MODEL": "test-model",
        }
    )
    storage_dir = tempfile.mkdtemp(prefix="sff-tests-storage-")
    os.environ["STORAGE_LOCAL_PATH"] = storage_dir
    workdir = tempfile.mkdtemp(prefix="sff-tests-work-")
    os.environ["WORKER_TMP_DIR"] = workdir

    # Cached storage instance must be rebuilt now that env is set.
    from storage import get_storage as _gs

    _gs.cache_clear()


# --------------------------------------------------------------------------
# fakeredis for the publish_sync helper.
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> fakeredis.FakeRedis:
    """Replace the cached redis client with a FakeRedis instance."""
    server = fakeredis.FakeServer()
    client = fakeredis.FakeRedis(server=server, decode_responses=False)

    import apps.workers.event_publisher as ep

    ep.reset_client_cache()
    monkeypatch.setattr(ep, "_client", lambda: client)
    return client


# --------------------------------------------------------------------------
# In-memory sqlite DB with a sqlite-friendly schema.
#
# The Postgres-only types (PG_UUID, JSONB) work fine on sqlite when we tell
# SQLAlchemy "compile JSONB as JSON" and "treat PG_UUID as str via the
# `as_uuid` flag and a CHAR(36) backend". We register the dialect-level
# compile shim once, then create the tables.
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def sqlite_db(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Use a temp sqlite file (not :memory:) so multiple connections see the same data."""
    from database.base import Base

    db_path = Path(tempfile.mkdtemp(prefix="sff-tests-db-")) / "test.sqlite"
    dsn = f"sqlite+aiosqlite:///{db_path}"

    # Force db_ctx to use this DSN.
    monkeypatch.setenv("DATABASE_URL", dsn)

    from sqlalchemy.dialects.postgresql import JSONB

    # SQLAlchemy registers compilers for JSONB on the postgresql dialect only;
    # on sqlite, JSONB columns are emitted as "JSONB" which sqlite then
    # treats as "BLOB". Override the compile to fall back to JSON.
    from sqlalchemy.ext.compiler import compiles

    @compiles(JSONB, "sqlite")
    def _jsonb_to_json_sqlite(type_: JSONB, compiler: Any, **kw: Any) -> str:
        return "JSON"

    @compiles(PG_UUID, "sqlite")
    def _uuid_to_char_sqlite(type_: PG_UUID, compiler: Any, **kw: Any) -> str:
        return "CHAR(36)"

    def _patch_enum_columns(declarative_base: Any) -> None:
        """Force SAEnum columns to store the enum *value* (not name) on sqlite.

        The shared models use ``SAEnum(JobStatus, native_enum=True, ...)`` which,
        without ``values_callable``, persists the enum **name** (``QUEUED``)
        rather than its **value** (``queued``). The production Postgres
        migration creates an enum type whose labels are the lowercase values,
        so the model-as-defined would also be wrong on Postgres -- but on
        sqlite native enums collapse to a CHECK constraint and we hit the
        problem now. Patch the enum binding to use values.
        """
        from sqlalchemy import Enum as SAEnum

        for table in declarative_base.metadata.tables.values():
            for col in table.columns:
                if isinstance(col.type, SAEnum) and col.type.enum_class is not None:
                    enum_cls = col.type.enum_class
                    col.type.values_callable = lambda c=enum_cls: [e.value for e in c]
                    # On sqlite we don't want a native enum type; force CHECK.
                    col.type.native_enum = False

    def _strip_pg_server_defaults(declarative_base: Any) -> None:
        """Remove Postgres-only server defaults so create_all works on sqlite.

        Also wires the columns up with sqlite-friendly python-side defaults
        so the ORM-level inserts (without ``server_default``) still produce
        non-null values for timestamps and JSON columns.
        """
        from datetime import datetime
        from sqlalchemy import DateTime
        from sqlalchemy.schema import ColumnDefault
        from sqlalchemy.types import JSON

        for table in declarative_base.metadata.tables.values():
            for col in table.columns:
                sd = col.server_default
                if sd is not None:
                    text = str(getattr(sd, "arg", ""))
                    drop = any(
                        s in text
                        for s in (
                            "gen_random_uuid",
                            "::",
                            "now()",
                            "'[]'::jsonb",
                            "'INFO'",
                            "0.0",
                            "'queued'",
                            "'planned'",
                        )
                    )
                    if drop:
                        col.server_default = None
                        # If the column is a timestamp, add a python default.
                        if isinstance(col.type, DateTime) and col.default is None:
                            ColumnDefault(datetime.utcnow)._set_parent(col)
                        # Default empty list for JSON columns.
                        from sqlalchemy.dialects.postgresql import JSONB

                        if isinstance(col.type, (JSON, JSONB)) and col.default is None:
                            ColumnDefault(list)._set_parent(col)
                # Ensure JSON columns with `default=list` survive the
                # sqlite roundtrip (no-op if already set on the model).
                from sqlalchemy.dialects.postgresql import JSONB

                if (
                    isinstance(col.type, (JSON, JSONB))
                    and col.default is None
                    and col.nullable is False
                ):
                    ColumnDefault(list)._set_parent(col)

    # Strip Postgres-only server_defaults (gen_random_uuid, now(), '[]'::jsonb).
    # We do this once per session by walking metadata before create_all.
    _strip_pg_server_defaults(Base)
    _patch_enum_columns(Base)

    # Bring up tables.
    async def _setup() -> None:
        engine = create_async_engine(dsn, future=True)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
                await conn.run_sync(Base.metadata.create_all)
        finally:
            await engine.dispose()

    asyncio.run(_setup())

    # Make a sessionmaker available to tests that want to introspect.
    sessionmaker = async_sessionmaker(
        create_async_engine(dsn, future=True),
        class_=AsyncSession,
        expire_on_commit=False,
    )
    yield sessionmaker


# --------------------------------------------------------------------------
# Helpers exposed to tests.
# --------------------------------------------------------------------------


@pytest.fixture
def make_job(sqlite_db: Any) -> Any:
    """Insert a Job row and return its id (str)."""
    from database.models import Job
    from shared_py.enums import JobStatus

    def _make(source_url: str = "https://example.test/video") -> str:
        async def _body() -> str:
            async with sqlite_db() as session:
                job = Job(
                    id=uuid.uuid4(),
                    source_url=source_url,
                    status=JobStatus.QUEUED,
                    progress_pct=0.0,
                )
                session.add(job)
                await session.commit()
                return str(job.id)

        return asyncio.run(_body())

    return _make

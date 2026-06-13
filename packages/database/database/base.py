"""Async SQLAlchemy engine, session factory and Declarative base.

The engine and session factory are constructed lazily so this module is safe to
import in scripts (Alembic env.py, seed.py) that supply their own DSN.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

# Consistent naming convention so Alembic generates stable constraint names.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base. All ORM models inherit from this."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


_engine: AsyncEngine | None = None
_factory: async_sessionmaker[AsyncSession] | None = None


def create_async_engine_from_url(url: str, **kwargs: Any) -> AsyncEngine:
    """Build and cache the async engine + session factory. Idempotent per URL."""
    global _engine, _factory
    if _engine is None:
        _engine = create_async_engine(
            url,
            pool_pre_ping=True,
            pool_size=kwargs.pop("pool_size", 10),
            max_overflow=kwargs.pop("max_overflow", 20),
            future=True,
            **kwargs,
        )
        _factory = async_sessionmaker(
            _engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _engine


def session_factory() -> async_sessionmaker[AsyncSession]:
    if _factory is None:
        raise RuntimeError(
            "Async engine not initialized; call create_async_engine_from_url(url) first."
        )
    return _factory


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency. Yields an AsyncSession, commits on success."""
    factory = session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

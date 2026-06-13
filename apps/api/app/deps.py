"""FastAPI dependency providers: database session, redis client, celery app."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from celery import Celery
from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app import celery_client
from app.config import Settings, get_settings


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield an AsyncSession tied to the per-request transaction.

    The session factory is created in `main.lifespan` and stored on
    `app.state.session_factory`.
    """
    factory = request.app.state.session_factory
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def get_redis(request: Request) -> Redis:
    """Return the shared async redis client from app state."""
    redis: Redis = request.app.state.redis
    return redis


def get_celery() -> Celery:
    return celery_client.celery_app()


SessionDep = Annotated[AsyncSession, Depends(get_session)]
RedisDep = Annotated[Redis, Depends(get_redis)]
CeleryDep = Annotated[Celery, Depends(get_celery)]
SettingsDep = Annotated[Settings, Depends(get_settings)]

"""FastAPI application factory.

Lifespan owns:

- Async SQLAlchemy engine + session factory (from `packages/database`).
- Redis async client used for SSE pub/sub.
- Loguru configuration.

Routers are mounted under their own prefixes. CORS, request IDs and structured
error responses are all wired up here.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings, get_settings
from app.errors import register_exception_handlers
from app.logging_setup import configure_logging
from app.routers import assets as assets_router
from app.routers import content_sources as content_sources_router
from app.routers import discover as discover_router
from app.routers import health as health_router
from app.routers import jobs as jobs_router
# Phase 2A: Facebook
from app.routers import facebook as facebook_router
# Phase 2C: Reel drafts
from app.routers import reel_drafts as reel_drafts_router
# Phase 3: Image posts
from app.routers import image_posts as image_posts_router
# Phase 4: Scripts & Dashboard
from app.routers import scripts as scripts_router
from app.routers import dashboard as dashboard_router


def _build_session_factory(settings: Settings) -> tuple[Any, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        settings.DATABASE_URL,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
        future=True,
    )
    factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    return engine, factory


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    configure_logging(settings.LOG_LEVEL)

    # Allow tests / wiring to pre-populate engine/factory/redis on app.state.
    if not getattr(app.state, "session_factory", None):
        engine, factory = _build_session_factory(settings)
        app.state.engine = engine
        app.state.session_factory = factory
    if not getattr(app.state, "redis", None):
        app.state.redis = Redis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=False,
        )

    logger.info("API service starting on port {}", settings.API_PORT)
    try:
        yield
    finally:
        logger.info("API service shutting down")
        try:
            redis: Redis | None = getattr(app.state, "redis", None)
            if redis is not None:
                await redis.aclose()
        except Exception as exc:  # pragma: no cover
            logger.warning("Error closing redis: {}", exc)
        try:
            engine = getattr(app.state, "engine", None)
            if engine is not None:
                await engine.dispose()
        except Exception as exc:  # pragma: no cover
            logger.warning("Error disposing DB engine: {}", exc)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a fresh FastAPI app. Used by uvicorn and the test suite."""
    settings = settings or get_settings()
    configure_logging(settings.LOG_LEVEL)

    app = FastAPI(
        title="Shortform Factory API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def _request_id_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        with logger.contextualize(request_id=rid, path=request.url.path, method=request.method):
            response = await call_next(request)
        response.headers["x-request-id"] = rid
        return response

    register_exception_handlers(app)

    app.include_router(health_router.router)
    app.include_router(jobs_router.router)
    app.include_router(assets_router.router)
    app.include_router(discover_router.router)
    app.include_router(content_sources_router.router)  # Phase 2B: Discovery
    app.include_router(facebook_router.router)  # Phase 2A: Facebook
    app.include_router(reel_drafts_router.router)  # Phase 2C: Reel drafts
    app.include_router(image_posts_router.router)  # Phase 3: Image posts
    app.include_router(scripts_router.router)  # Phase 4: Scripts
    app.include_router(dashboard_router.router)  # Phase 4: Dashboard

    return app


app = create_app()

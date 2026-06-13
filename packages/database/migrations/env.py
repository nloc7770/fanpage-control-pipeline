"""Alembic environment.

Reads the DSN from `DATABASE_URL_SYNC` (preferred) or `DATABASE_URL`, falling
back to alembic.ini. Runs migrations synchronously using psycopg.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from database.base import Base
from database import models  # noqa: F401  -- ensure models are imported

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _resolve_url() -> str:
    sync_url = os.getenv("DATABASE_URL_SYNC")
    if sync_url:
        return sync_url
    async_url = os.getenv("DATABASE_URL")
    if async_url:
        # Swap asyncpg driver for a sync driver Alembic can use.
        return (
            async_url.replace("+asyncpg", "+psycopg")
            .replace("postgresql://", "postgresql+psycopg://")
        )
    return config.get_main_option("sqlalchemy.url") or ""


config.set_main_option("sqlalchemy.url", _resolve_url())

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section) or {},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

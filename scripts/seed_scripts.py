"""Seed script: insert the 10 video scripts into the scripts table.

Usage:
    docker compose exec -T api python /app/scripts/seed_scripts.py
"""

from __future__ import annotations

import asyncio
import os
import sys

# Ensure packages are importable
sys.path.insert(0, "/app/apps/api")
sys.path.insert(0, "/app/packages/shared-py")
sys.path.insert(0, "/app/packages/database")

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://factory:factory@postgres:5432/factory",
)

SCRIPTS_DATA = [
    ("video-01-gioi-thieu", "Giới thiệu - Ngày 1", 60),
    ("video-02-buoi-tap-dau", "Buổi tập đầu tiên sau 2 năm", 90),
    ("video-03-so-lieu-ngay-1", "Số liệu khởi điểm — Ngày 1", 45),
    ("video-04-tai-sao-bat-dau", "Tại sao mình bắt đầu lại", 90),
    ("video-05-an-gi-tang-can", "Ăn gì để tăng cân", 60),
    ("video-06-ngay-thu-3", "Ngày thứ 3", 60),
    ("video-07-dev-va-gym", "Developer và Gym", 60),
    ("video-08-con-khoc-dem", "Con khóc đêm vẫn đi tập", 60),
    ("video-09-sai-lam-quay-lai", "Sai lầm khi quay lại gym", 60),
    ("video-10-weekly-recap", "Tuần đầu tiên - Tổng kết", 90),
]

SCRIPTS_DIR = os.environ.get("SCRIPTS_DIR", "/data/storage/scripts")


async def seed() -> None:
    engine = create_async_engine(DATABASE_URL, echo=False)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        for slug, title, duration in SCRIPTS_DATA:
            file_path = f"{SCRIPTS_DIR}/{slug}.md"
            await session.execute(
                text("""
                    INSERT INTO scripts (slug, title, status, file_path, duration_seconds)
                    VALUES (:slug, :title, 'unfilmed', :file_path, :duration)
                    ON CONFLICT (slug) DO NOTHING
                """),
                {"slug": slug, "title": title, "file_path": file_path, "duration": duration},
            )
        await session.commit()
        print(f"Seeded {len(SCRIPTS_DATA)} scripts.")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())

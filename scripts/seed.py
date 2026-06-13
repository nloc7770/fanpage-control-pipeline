"""Seed a demo job into the database.

Usage:
    python scripts/seed.py [SOURCE_URL]

Runs inside the api container under `make seed`.
"""

from __future__ import annotations

import asyncio
import os
import sys

from sqlalchemy import select

from database import Job, create_async_engine_from_url, session_factory
from shared_py.enums import JobStatus

DEFAULT_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


async def main(source_url: str) -> None:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not set", file=sys.stderr)
        sys.exit(2)

    create_async_engine_from_url(db_url)

    async with session_factory()() as session:
        existing = await session.scalar(
            select(Job).where(Job.source_url == source_url).limit(1)
        )
        if existing is not None:
            print(f"Job already exists for {source_url}: {existing.id}")
            return

        job = Job(
            source_url=source_url,
            status=JobStatus.QUEUED,
            progress_pct=0.0,
            current_stage="queued",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        print(f"Seeded job {job.id} for {source_url}")


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    asyncio.run(main(url))

"""GET /jobs listing tests."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from database.models import Job
from shared_py.enums import JobStatus


@pytest.mark.asyncio
async def test_list_jobs_pagination_and_status_filter(
    client: AsyncClient,
    celery_calls: list[dict[str, object]],  # noqa: ARG001
    engine_and_factory,
) -> None:
    _engine, factory = engine_and_factory
    async with factory() as session:
        for i in range(3):
            session.add(
                Job(
                    source_url=f"https://example.com/{i}",
                    status=JobStatus.QUEUED if i < 2 else JobStatus.COMPLETED,
                    progress_pct=0.0,
                )
            )
        await session.commit()

    resp = await client.get("/jobs?limit=10&offset=0")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert len(body["jobs"]) == 3

    resp = await client.get("/jobs?status=queued")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    for j in body["jobs"]:
        assert j["status"] == "queued"


@pytest.mark.asyncio
async def test_get_job_detail_and_404(
    client: AsyncClient,
    celery_calls: list[dict[str, object]],  # noqa: ARG001
    engine_and_factory,
) -> None:
    _engine, factory = engine_and_factory
    async with factory() as session:
        job = Job(source_url="https://example.com/detail", status=JobStatus.QUEUED, progress_pct=0.0)
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = str(job.id)

    ok = await client.get(f"/jobs/{job_id}")
    assert ok.status_code == 200
    assert ok.json()["id"] == job_id

    missing = await client.get("/jobs/00000000-0000-0000-0000-000000000000")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"

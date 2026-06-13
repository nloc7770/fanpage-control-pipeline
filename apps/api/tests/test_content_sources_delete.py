"""DELETE /content-sources/{id} hard-delete cascade tests."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from database.models import (
    ContentSource,
    FacebookAccount,
    FacebookPage,
    Job,
    PublishJob,
    ReelDraft,
)
from shared_py.enums import (
    ContentSourceStatus,
    FacebookAccountStatus,
    FacebookPageStatus,
    JobStatus,
)


async def _seed_page(factory) -> tuple[UUID, UUID]:
    """Insert a FacebookAccount + FacebookPage. Return (account_id, page_id)."""
    async with factory() as session:
        account = FacebookAccount(
            provider_user_id=f"u-{uuid4().hex[:8]}",
            display_name="Test User",
            encrypted_access_token="enc::stub",
            status=FacebookAccountStatus.ACTIVE,
        )
        session.add(account)
        await session.flush()
        page = FacebookPage(
            account_id=account.id,
            page_id=f"p-{uuid4().hex[:8]}",
            page_name="Test Page",
            avatar_url="https://example.com/a.png",
            encrypted_page_access_token="enc::stub",
            status=FacebookPageStatus.ACTIVE,
        )
        session.add(page)
        await session.commit()
        return account.id, page.id


@pytest.mark.asyncio
async def test_delete_content_source_404(client: AsyncClient) -> None:
    resp = await client.delete("/content-sources/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_delete_content_source_cascades_jobs_and_drafts(
    client: AsyncClient,
    engine_and_factory,
) -> None:
    _engine, factory = engine_and_factory
    _account_id, page_id = await _seed_page(factory)

    # Seed: one ContentSource + one Job linked via source_metadata + one ReelDraft + PublishJob.
    source_id: UUID
    job_id: UUID
    async with factory() as session:
        source = ContentSource(
            page_id=page_id,
            platform="youtube",
            source_url="https://youtube.com/watch?v=abc",
            status=ContentSourceStatus.QUEUED,
        )
        session.add(source)
        await session.flush()
        source_id = source.id

        job = Job(
            source_url="https://youtube.com/watch?v=abc",
            status=JobStatus.QUEUED,
            progress_pct=0.0,
            source_metadata={
                "source_type": "auto_discovery",
                "content_source_id": str(source_id),
                "facebook_page_id": str(page_id),
            },
        )
        # An unrelated job should NOT be touched.
        unrelated = Job(
            source_url="https://example.com/other",
            status=JobStatus.QUEUED,
            progress_pct=0.0,
            source_metadata={"source_type": "manual"},
        )
        session.add_all([job, unrelated])
        await session.flush()
        job_id = job.id
        unrelated_id = unrelated.id

        draft = ReelDraft(
            page_id=page_id,
            content_source_id=source_id,
            title="Draft",
        )
        session.add(draft)
        await session.flush()
        publish = PublishJob(
            reel_draft_id=draft.id,
            page_id=page_id,
        )
        session.add(publish)
        await session.commit()

    # Act.
    resp = await client.delete(f"/content-sources/{source_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source_id"] == str(source_id)
    assert body["deleted_jobs"] == 1
    assert body["deleted_reel_drafts"] == 1
    assert body["deleted_publish_jobs"] == 1
    assert body["deleted_files"] == 0
    assert body["freed_bytes"] == 0

    # Assert DB state.
    async with factory() as session:
        assert (
            await session.execute(select(ContentSource).where(ContentSource.id == source_id))
        ).scalar_one_or_none() is None
        assert (
            await session.execute(select(Job).where(Job.id == job_id))
        ).scalar_one_or_none() is None
        # Unrelated job survives.
        assert (
            await session.execute(select(Job).where(Job.id == unrelated_id))
        ).scalar_one_or_none() is not None
        assert (
            await session.execute(
                select(ReelDraft).where(ReelDraft.content_source_id == source_id)
            )
        ).scalar_one_or_none() is None
        assert (
            await session.execute(select(PublishJob))
        ).scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_delete_content_source_removes_local_files(
    client: AsyncClient,
    engine_and_factory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _engine, factory = engine_and_factory
    _account_id, page_id = await _seed_page(factory)

    # Point STORAGE_LOCAL_PATH at tmp.
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("STORAGE_LOCAL_PATH", str(tmp_path))

    # Seed source + job.
    async with factory() as session:
        source = ContentSource(
            page_id=page_id,
            platform="youtube",
            source_url="https://youtube.com/watch?v=xyz",
            status=ContentSourceStatus.GENERATED,
        )
        session.add(source)
        await session.flush()
        source_id = source.id

        job = Job(
            source_url="https://youtube.com/watch?v=xyz",
            status=JobStatus.COMPLETED,
            progress_pct=100.0,
            source_metadata={
                "source_type": "auto_discovery",
                "content_source_id": str(source_id),
            },
        )
        session.add(job)
        await session.flush()
        job_id = job.id
        await session.commit()

    # Drop a fake artefact under storage/{job_id}/.
    job_dir = tmp_path / str(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    f = job_dir / "test.bin"
    payload = b"x" * 4096
    f.write_bytes(payload)
    assert f.exists()

    resp = await client.delete(f"/content-sources/{source_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deleted_jobs"] == 1
    assert body["deleted_files"] >= 1
    assert body["freed_bytes"] >= len(payload)
    assert not f.exists()
    assert not job_dir.exists()

    get_settings.cache_clear()

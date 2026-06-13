"""Unit tests for the discover service.

We mock `yt_dlp.YoutubeDL.extract_info` with deterministic fixture data so
the tests are fast and offline. Each behaviour the spec calls out (filter
threshold, rank order, dedupe across queries) has its own test.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

import pytest

from services.discover import runner
from services.discover.runner import (
    VideoCandidate,
    _age_days,
    _expand_queries,
    _looks_english,
    discover,
    filter_candidates,
    rank_candidates,
    search_videos,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _entry(
    vid: str,
    title: str,
    views: int = 100_000,
    duration: float = 600.0,
    channel: str = "Test Channel",
    channel_id: str = "UC_test",
    upload_date: str = "",
) -> dict[str, Any]:
    return {
        "id": vid,
        "title": title,
        "view_count": views,
        "duration": duration,
        "channel": channel,
        "channel_id": channel_id,
        "upload_date": upload_date,
        "url": f"https://www.youtube.com/watch?v={vid}",
    }


def _today_offset(days: int) -> str:
    """Return a YYYYMMDD string for `days` ago (UTC)."""
    d = datetime.now(timezone.utc) - timedelta(days=days)
    return d.strftime("%Y%m%d")


# ---------------------------------------------------------------------------
# search_videos
# ---------------------------------------------------------------------------


def test_search_videos_dedupes_by_video_id():
    """Same video returned by multiple query variants must appear once."""
    fixtures: dict[str, list[dict[str, Any]]] = {
        "tarpon fishing": [_entry("v1", "Tarpon!", views=500_000)],
        "tarpon fishing catch big": [_entry("v1", "Tarpon!", views=500_000)],
        "tarpon fishing best": [_entry("v2", "Other", views=200_000)],
        "tarpon fishing highlights": [],
        "tarpon fishing compilation": [],
    }

    def fake_search(query: str, per_query: int) -> list[dict[str, Any]]:
        return fixtures.get(query, [])

    with patch.object(runner, "_ydl_search", side_effect=fake_search):
        # Bypass the on-disk cache so test ordering doesn't leak.
        with patch.object(runner, "_cache_get", return_value=None), \
             patch.object(runner, "_cache_put"):
            cands = search_videos("tarpon fishing", per_query=5, max_results=10)
    ids = sorted(c.video_id for c in cands)
    assert ids == ["v1", "v2"]


def test_search_videos_caps_at_max_results():
    """The top-views truncation guarantees we don't blow past max_results."""
    big_batch = [_entry(f"v{i}", f"vid {i}", views=1_000 * i) for i in range(50)]

    with patch.object(runner, "_ydl_search", return_value=big_batch):
        with patch.object(runner, "_cache_get", return_value=None), \
             patch.object(runner, "_cache_put"):
            cands = search_videos("anything", per_query=50, max_results=10)

    assert len(cands) == 10
    # Highest views must survive the truncation.
    assert cands[0].views == 49_000


# ---------------------------------------------------------------------------
# filter_candidates
# ---------------------------------------------------------------------------


def _candidate(**kw: Any) -> VideoCandidate:
    base = dict(
        video_id="vx",
        url="https://youtu.be/vx",
        title="A great fishing video",
        channel="Cool Channel",
        channel_id="UC_cool",
        views=100_000,
        duration_s=600.0,
        upload_date="",
    )
    base.update(kw)
    return VideoCandidate(**base)


def test_filter_drops_low_views():
    cands = [
        _candidate(video_id="lo", views=10_000),
        _candidate(video_id="hi", views=200_000),
    ]
    out = filter_candidates(cands, min_views=50_000)
    assert [c.video_id for c in out] == ["hi"]


def test_filter_drops_shorts_unconditionally():
    """Shorts (<60s) must be dropped even when min_duration_s is lower."""
    cands = [
        _candidate(video_id="short", duration_s=30.0, views=10_000_000),
        _candidate(video_id="ok", duration_s=600.0, views=100_000),
    ]
    out = filter_candidates(cands, min_views=10_000, min_duration_s=10.0)
    assert [c.video_id for c in out] == ["ok"]


def test_filter_drops_too_long():
    cands = [
        _candidate(video_id="movie", duration_s=7200.0),
        _candidate(video_id="ok", duration_s=600.0),
    ]
    out = filter_candidates(cands)
    assert [c.video_id for c in out] == ["ok"]


def test_filter_blocks_channels():
    cands = [
        _candidate(video_id="a", channel="Banned"),
        _candidate(video_id="b", channel="OK"),
    ]
    out = filter_candidates(cands, blocked_channels={"Banned"})
    assert [c.video_id for c in out] == ["b"]


def test_filter_language_english_only():
    """Vietnamese diacritics and CJK should fail the English heuristic."""
    cands = [
        _candidate(video_id="vi", title="Câu cá Việt Nam siêu lớn"),
        _candidate(video_id="cn", title="钓鱼 大鱼"),
        _candidate(video_id="en", title="Massive tarpon caught on light tackle"),
    ]
    out = filter_candidates(cands, require_english=True)
    assert [c.video_id for c in out] == ["en"]


def test_filter_language_off_keeps_everything():
    cands = [
        _candidate(video_id="vi", title="Câu cá Việt Nam"),
        _candidate(video_id="en", title="Tarpon strike"),
    ]
    out = filter_candidates(cands, require_english=False)
    assert {c.video_id for c in out} == {"vi", "en"}


def test_filter_age_keeps_unknown_dates():
    """Missing upload_date must NOT cause rejection (we'll hydrate later)."""
    cands = [
        _candidate(video_id="no_date", upload_date=""),
        _candidate(video_id="old", upload_date=_today_offset(400)),
        _candidate(video_id="new", upload_date=_today_offset(10)),
    ]
    out = filter_candidates(cands, max_age_days=180)
    assert sorted(c.video_id for c in out) == ["new", "no_date"]


# ---------------------------------------------------------------------------
# rank_candidates
# ---------------------------------------------------------------------------


def test_rank_orders_high_views_first():
    cands = [
        _candidate(video_id="lo", views=80_000),
        _candidate(video_id="mid", views=500_000),
        _candidate(video_id="hi", views=5_000_000),
    ]
    ranked = rank_candidates(cands, topic="fishing")
    assert [c.video_id for c in ranked] == ["hi", "mid", "lo"]
    # Scores monotonically decrease.
    assert ranked[0].score > ranked[1].score > ranked[2].score


def test_rank_topic_match_boost():
    """A title that contains the topic token beats one that doesn't, all else equal."""
    cands = [
        _candidate(video_id="off", title="random unrelated video", views=200_000),
        _candidate(video_id="on", title="tarpon fishing trip", views=200_000),
    ]
    ranked = rank_candidates(cands, topic="tarpon")
    assert ranked[0].video_id == "on"
    assert any("topic match" in r for r in ranked[0].reasons)


def test_rank_duration_sweet_spot():
    """A 10-minute video must outscore an identical-views 2-hour video."""
    cands = [
        _candidate(video_id="long", duration_s=7000.0, views=200_000),
        _candidate(video_id="sweet", duration_s=600.0, views=200_000),
    ]
    ranked = rank_candidates(cands, topic="fishing")
    assert ranked[0].video_id == "sweet"


def test_rank_recency_boost():
    cands = [
        _candidate(video_id="old", upload_date=_today_offset(365), views=200_000),
        _candidate(video_id="new", upload_date=_today_offset(7), views=200_000),
    ]
    ranked = rank_candidates(cands)
    assert ranked[0].video_id == "new"
    assert any("recent" in r for r in ranked[0].reasons)


# ---------------------------------------------------------------------------
# discover (end-to-end with mocks)
# ---------------------------------------------------------------------------


def test_discover_end_to_end_returns_top_n():
    """End-to-end with mocked yt-dlp: filter then rank then truncate."""
    fixtures = [
        _entry("a", "tarpon big strike", views=2_000_000, duration=600, upload_date=_today_offset(30)),
        _entry("b", "tarpon catch", views=300_000, duration=400, upload_date=_today_offset(60)),
        _entry("c", "low views vid", views=1_000, duration=400, upload_date=_today_offset(10)),
        _entry("d", "tarpon highlights", views=800_000, duration=900, upload_date=_today_offset(5)),
        _entry("e", "short clip", views=5_000_000, duration=40),  # Short, must be dropped
    ]

    with patch.object(runner, "_ydl_search", return_value=fixtures), \
         patch.object(runner, "_cache_get", return_value=None), \
         patch.object(runner, "_cache_put"), \
         patch.object(runner, "_ydl_lookup_upload_date", return_value=""):
        results = discover("tarpon", top=3, min_views=50_000, max_age_days=180)

    ids = [c.video_id for c in results]
    # Three survivors (a, b, d) ranked by score (highest views + recency + match).
    assert len(results) == 3
    assert "c" not in ids  # low views
    assert "e" not in ids  # Short
    assert results[0].score >= results[-1].score
    # 'a' has the most views by an order of magnitude; should be #1.
    assert results[0].video_id == "a"


# ---------------------------------------------------------------------------
# Misc helpers
# ---------------------------------------------------------------------------


def test_expand_queries_dedupe_and_first_is_original():
    qs = _expand_queries("Tarpon Fishing")
    assert qs[0] == "Tarpon Fishing"
    # No exact duplicates regardless of case.
    assert len(qs) == len({q.lower() for q in qs})


def test_age_days_parses_yyyymmdd():
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    assert _age_days(today) == 0
    assert _age_days("") is None
    assert _age_days("bogus") is None


def test_looks_english_handles_diacritics():
    assert _looks_english("Tarpon fishing!")
    assert not _looks_english("Câu cá")
    assert not _looks_english("钓鱼大全")


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])

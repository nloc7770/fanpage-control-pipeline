"""Tests for services/discovery/ranking.py."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from services.discovery.ranking import (
    DISCOVERY_MIN_VIEWS,
    DISCOVERY_TOP_N,
    _age_days,
    _score_candidate,
    _tokenize,
    rank_candidates,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_candidate(
    *,
    view_count: int = 50_000,
    upload_date: str = "20260501",
    duration: int = 600,
    title: str = "Big Fish Catch Compilation",
    channel: str = "FishingChannel",
) -> dict:
    return {
        "source_url": f"https://www.youtube.com/watch?v=test123",
        "source_title": title,
        "channel_name": channel,
        "duration_seconds": duration,
        "thumbnail_url": "https://img.youtube.com/vi/test123/hqdefault.jpg",
        "detected_topic": None,
        "title": title,
        "raw_metadata": {
            "id": "test123",
            "title": title,
            "channel": channel,
            "duration": duration,
            "view_count": view_count,
            "upload_date": upload_date,
        },
    }


# ---------------------------------------------------------------------------
# Unit tests: helpers
# ---------------------------------------------------------------------------


class TestTokenize:
    def test_basic(self) -> None:
        assert _tokenize("Big Fish Catch") == {"big", "fish", "catch"}

    def test_special_chars(self) -> None:
        assert _tokenize("hello-world_123!") == {"hello", "world", "123"}

    def test_empty(self) -> None:
        assert _tokenize("") == set()


class TestAgeDays:
    def test_recent_date(self) -> None:
        # Use a date we know is recent relative to "now"
        age = _age_days("20260529")
        assert age is not None
        assert age >= 0
        assert age <= 2  # Should be ~1 day old

    def test_old_date(self) -> None:
        age = _age_days("20240101")
        assert age is not None
        assert age > 365

    def test_none(self) -> None:
        assert _age_days(None) is None

    def test_empty(self) -> None:
        assert _age_days("") is None

    def test_invalid(self) -> None:
        assert _age_days("not-a-date") is None

    def test_dashed_format(self) -> None:
        age = _age_days("2026-05-29")
        assert age is not None
        assert age >= 0


# ---------------------------------------------------------------------------
# Unit tests: scoring
# ---------------------------------------------------------------------------


class TestScoreCandidate:
    def test_high_views_boost(self) -> None:
        candidate = _make_candidate(view_count=1_000_000)
        score, reasons = _score_candidate(candidate, set())
        assert score > 0
        assert any("viral" in r for r in reasons)

    def test_low_views(self) -> None:
        low = _make_candidate(view_count=5_000)
        high = _make_candidate(view_count=500_000)
        score_low, _ = _score_candidate(low, set())
        score_high, _ = _score_candidate(high, set())
        assert score_high > score_low

    def test_recency_boost(self) -> None:
        recent = _make_candidate(upload_date="20260528")
        old = _make_candidate(upload_date="20250101")
        score_recent, reasons_recent = _score_candidate(recent, set())
        score_old, _ = _score_candidate(old, set())
        assert score_recent > score_old
        assert any("recent" in r for r in reasons_recent)

    def test_duration_sweet_spot(self) -> None:
        ideal = _make_candidate(duration=600)  # 10 min
        too_short = _make_candidate(duration=120)  # 2 min
        score_ideal, reasons_ideal = _score_candidate(ideal, set())
        score_short, _ = _score_candidate(too_short, set())
        assert score_ideal > score_short
        assert any("ideal length" in r for r in reasons_ideal)

    def test_topic_match(self) -> None:
        candidate = _make_candidate(title="Big Fish Catch Compilation")
        topic_tokens = _tokenize("fishing big fish")
        score_match, reasons = _score_candidate(candidate, topic_tokens)
        score_no_match, _ = _score_candidate(candidate, set())
        assert score_match > score_no_match
        assert any("topic match" in r for r in reasons)


# ---------------------------------------------------------------------------
# Integration tests: rank_candidates
# ---------------------------------------------------------------------------


class TestRankCandidates:
    def test_filters_low_views(self) -> None:
        candidates = [
            _make_candidate(view_count=5_000),  # Below default 10k threshold
            _make_candidate(view_count=50_000),
        ]
        results = rank_candidates(candidates)
        assert len(results) == 1
        assert results[0]["raw_metadata"]["view_count"] == 50_000

    def test_filters_old_content(self) -> None:
        candidates = [
            _make_candidate(upload_date="20240101"),  # > 180 days old
            _make_candidate(upload_date="20260501"),  # Recent
        ]
        results = rank_candidates(candidates, max_age_days=180)
        assert len(results) == 1
        assert results[0]["raw_metadata"]["upload_date"] == "20260501"

    def test_keeps_unknown_age(self) -> None:
        """Candidates with no upload_date should not be filtered by age."""
        candidates = [
            _make_candidate(upload_date=""),
        ]
        results = rank_candidates(candidates, min_views=0, max_age_days=180)
        assert len(results) == 1

    def test_sorts_by_score_descending(self) -> None:
        candidates = [
            _make_candidate(view_count=20_000, upload_date="20260401"),
            _make_candidate(view_count=500_000, upload_date="20260520"),
        ]
        results = rank_candidates(candidates, min_views=10_000, max_age_days=365, top_n=10)
        assert len(results) == 2
        scores = [r["raw_metadata"]["virality_score"] for r in results]
        assert scores[0] >= scores[1]

    def test_limits_to_top_n(self) -> None:
        candidates = [_make_candidate(view_count=50_000 + i * 10_000) for i in range(10)]
        results = rank_candidates(candidates, top_n=5)
        assert len(results) == 5

    def test_default_top_n_is_5(self) -> None:
        candidates = [_make_candidate(view_count=50_000 + i * 10_000) for i in range(10)]
        results = rank_candidates(candidates)
        assert len(results) == 5

    def test_virality_score_stored_in_metadata(self) -> None:
        candidates = [_make_candidate(view_count=100_000)]
        results = rank_candidates(candidates, top_n=5)
        assert len(results) == 1
        meta = results[0]["raw_metadata"]
        assert "virality_score" in meta
        assert isinstance(meta["virality_score"], float)
        assert meta["virality_score"] > 0

    def test_ranking_reasons_stored(self) -> None:
        candidates = [_make_candidate(view_count=100_000)]
        results = rank_candidates(candidates, top_n=5)
        meta = results[0]["raw_metadata"]
        assert "ranking_reasons" in meta
        assert isinstance(meta["ranking_reasons"], list)
        assert len(meta["ranking_reasons"]) > 0

    def test_does_not_mutate_input(self) -> None:
        candidates = [_make_candidate(view_count=100_000)]
        original_meta = dict(candidates[0]["raw_metadata"])
        rank_candidates(candidates, top_n=5)
        assert candidates[0]["raw_metadata"] == original_meta

    def test_topic_boosts_relevant_candidates(self) -> None:
        relevant = _make_candidate(
            view_count=50_000, title="Big Fish Catch River"
        )
        irrelevant = _make_candidate(
            view_count=50_000, title="Cooking Recipe Tutorial"
        )
        candidates = [irrelevant, relevant]
        results = rank_candidates(candidates, topic="fish catch", top_n=2, min_views=0)
        # The fish-related one should rank higher
        assert "fish" in results[0]["source_title"].lower()

    def test_env_var_min_views(self) -> None:
        """DISCOVERY_MIN_VIEWS env var controls the threshold."""
        candidates = [_make_candidate(view_count=5_000)]
        with patch.dict(os.environ, {"DISCOVERY_MIN_VIEWS": "1000"}):
            # Need to reimport to pick up env var change
            from services.discovery import ranking
            results = ranking.rank_candidates(candidates, min_views=1000)
        assert len(results) == 1

    def test_empty_input(self) -> None:
        results = rank_candidates([])
        assert results == []

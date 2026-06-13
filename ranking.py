"""Ranking and scoring for automated YouTube discovery candidates.

Ported from ``services/discover/runner.rank_candidates`` and adapted for the
dict-based candidate format used by the automated pipeline.

Scoring factors:
- View count (log10 scale)
- Recency (boost for videos < 90 days old, extra boost for < 30 days)
- Duration sweet spot (5-15 min ideal)
- Engagement signals (view count thresholds)

Configuration:
- ``DISCOVERY_MIN_VIEWS``: minimum view count to keep a candidate (default 10000)
- ``DISCOVERY_MAX_AGE_DAYS``: maximum age in days (default 180)
- ``DISCOVERY_TOP_N``: number of top candidates to keep (default 5)
"""

from __future__ import annotations

import math
import os
import re
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DISCOVERY_MIN_VIEWS: int = int(os.environ.get("DISCOVERY_MIN_VIEWS", "10000"))
DISCOVERY_MAX_AGE_DAYS: int = int(os.environ.get("DISCOVERY_MAX_AGE_DAYS", "180"))
DISCOVERY_TOP_N: int = int(os.environ.get("DISCOVERY_TOP_N", "5"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> set[str]:
    """Split text into lowercase alphanumeric tokens."""
    return {t for t in re.split(r"[^a-z0-9]+", text.lower()) if t}


def _parse_upload_date(raw_date: str | None) -> datetime | None:
    """Parse YYYYMMDD or YYYY-MM-DD date string to datetime."""
    if not raw_date:
        return None
    # Normalize: strip dashes, take first 8 chars
    cleaned = raw_date.replace("-", "")[:8]
    if len(cleaned) < 8:
        return None
    try:
        return datetime.strptime(cleaned, "%Y%m%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _age_days(upload_date: str | None) -> int | None:
    """Return age in days for a date string, or None if unparseable."""
    dt = _parse_upload_date(upload_date)
    if dt is None:
        return None
    now = datetime.now(timezone.utc)
    delta = now - dt
    return max(0, int(delta.total_seconds() // 86400))


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _score_candidate(
    candidate: dict[str, Any],
    topic_tokens: set[str],
) -> tuple[float, list[str]]:
    """Score a single candidate dict. Returns (score, reasons)."""
    reasons: list[str] = []
    score = 0.0

    raw_meta = candidate.get("raw_metadata") or {}
    view_count = int(raw_meta.get("view_count") or 0)
    upload_date = raw_meta.get("upload_date") or ""
    duration = candidate.get("duration_seconds") or raw_meta.get("duration") or 0
    try:
        duration = int(float(duration))
    except (TypeError, ValueError):
        duration = 0

    # --- View count score (log10 scale) ---
    if view_count > 0:
        view_score = math.log10(max(view_count, 1)) * 10.0
        score += view_score
        if view_count >= 1_000_000:
            reasons.append(f"viral views ({view_count:,})")
        elif view_count >= 100_000:
            reasons.append(f"high views ({view_count:,})")
        else:
            reasons.append(f"views={view_count:,}")

    # --- Recency score ---
    age = _age_days(upload_date)
    if age is not None:
        if age < 30:
            # Extra boost for very recent content
            score += (30 - age) * 0.3 + 6.0
            reasons.append(f"very recent ({age}d old)")
        elif age < 90:
            score += (90 - age) * 0.1
            reasons.append(f"recent ({age}d old)")

    # --- Duration sweet spot (5-15 min = 300-900s) ---
    if 300 <= duration <= 900:
        score += 3.0
        reasons.append("ideal length (5-15m)")
    elif 180 <= duration <= 1500:
        score += 1.0
        reasons.append("acceptable length")
    else:
        score += 0.3

    # --- Topic match in title ---
    if topic_tokens:
        title = candidate.get("source_title") or candidate.get("title") or ""
        title_tokens = _tokenize(title)
        overlap = topic_tokens & title_tokens
        if overlap:
            score += 5.0
            reasons.append("topic match in title")

    return round(score, 3), reasons


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def rank_candidates(
    candidates: list[dict[str, Any]],
    *,
    topic: str | None = None,
    min_views: int | None = None,
    max_age_days: int | None = None,
    top_n: int | None = None,
) -> list[dict[str, Any]]:
    """Score, filter, sort, and limit discovery candidates.

    Applies the following pipeline:
    1. Filter by minimum view count (``DISCOVERY_MIN_VIEWS`` env or kwarg)
    2. Filter by maximum age (``DISCOVERY_MAX_AGE_DAYS`` env or kwarg)
    3. Score each surviving candidate
    4. Sort by score descending
    5. Keep only top N (``DISCOVERY_TOP_N`` env or kwarg)

    Each candidate dict gets ``virality_score`` and ``ranking_reasons`` added
    to its ``raw_metadata`` for downstream persistence.

    Returns a new list; input is not mutated.
    """
    _min_views = min_views if min_views is not None else DISCOVERY_MIN_VIEWS
    _max_age_days = max_age_days if max_age_days is not None else DISCOVERY_MAX_AGE_DAYS
    _top_n = top_n if top_n is not None else DISCOVERY_TOP_N
    topic_tokens = _tokenize(topic) if topic else set()

    scored: list[tuple[float, list[str], dict[str, Any]]] = []

    for candidate in candidates:
        raw_meta = candidate.get("raw_metadata") or {}
        view_count = int(raw_meta.get("view_count") or 0)
        upload_date = raw_meta.get("upload_date") or ""

        # --- Min views gate ---
        if view_count < _min_views:
            continue

        # --- Max age gate ---
        age = _age_days(upload_date)
        if age is not None and age > _max_age_days:
            continue

        # --- Score ---
        score, reasons = _score_candidate(candidate, topic_tokens)
        scored.append((score, reasons, candidate))

    # Sort by score descending
    scored.sort(key=lambda x: x[0], reverse=True)

    # Take top N and annotate with score
    results: list[dict[str, Any]] = []
    for score, reasons, candidate in scored[:_top_n]:
        # Create a shallow copy to avoid mutating the original
        ranked = {**candidate}
        raw_meta = dict(ranked.get("raw_metadata") or {})
        raw_meta["virality_score"] = score
        raw_meta["ranking_reasons"] = reasons
        ranked["raw_metadata"] = raw_meta
        results.append(ranked)

    return results

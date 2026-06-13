"""Core crawl + rank logic for the discover service.

Uses `yt_dlp` directly (no shelling out) and caches batch search responses
to `_storage_data/discover_cache.json` to keep iteration cheap during dev.
The public surface is:

- :func:`search_videos`   raw crawl across multiple query variants
- :func:`filter_candidates` keep videos that pass view / age / duration gates
- :func:`rank_candidates` score by views + recency + duration sweet spot
- :func:`discover`        end-to-end search -> filter -> rank -> top N
"""

from __future__ import annotations

import json
import math
import os
import re
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

try:
    import yt_dlp  # type: ignore
except ImportError:  # pragma: no cover - yt_dlp is a required dependency
    yt_dlp = None  # type: ignore


# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------

# Resolve the storage dir relative to the repo root regardless of cwd.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_STORAGE_DIR = os.environ.get(
    "STORAGE_LOCAL_PATH", os.path.join(_REPO_ROOT, "_storage_data")
)
_CACHE_PATH = os.path.join(_STORAGE_DIR, "discover_cache.json")
_CACHE_TTL_S = 6 * 3600  # 6 hours
_SHORT_THRESHOLD_S = 60.0  # skip YouTube Shorts


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class VideoCandidate:
    """One ranked candidate video.

    All fields are JSON-friendly so the dataclass round-trips through the
    cache and the REST DTO without bespoke serializers.
    """

    video_id: str
    url: str
    title: str
    channel: str
    channel_id: str
    views: int
    duration_s: float
    upload_date: str  # YYYYMMDD; empty string if unknown
    description: str = ""
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Query expansion
# ---------------------------------------------------------------------------


def _expand_queries(topic: str) -> list[str]:
    """Return ~5 search variants for `topic`.

    Adds modifiers like "catch big", "compilation", "best", "highlights" that
    tend to surface high-engagement videos. The original topic is always
    first so the most literal match takes priority.
    """
    base = topic.strip()
    if not base:
        return []
    modifiers = ["catch big", "best", "highlights", "compilation"]
    variants = [base]
    for m in modifiers:
        variants.append(f"{base} {m}")
    # Dedupe while preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for v in variants:
        key = v.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(v)
    return out


# Curated niche presets — Vietnam Facebook reup market.
# Each preset returns a set of high-yield search queries tuned for the niche.
# Used by the CLI/REST layer when caller passes a known preset name.
NICHE_PRESETS: dict[str, list[str]] = {
    "fishing": [
        "huge fish catch",
        "monster fish caught",
        "ocean fishing big catch",
        "river fishing compilation",
        "fishing strike moment",
        "deep sea fishing big fish",
        "tarpon fishing",
        "marlin fishing",
        "catfish monster catch",
        "fly fishing big fish",
    ],
    "survival": [
        "bushcraft solo wilderness",
        "primitive technology survival",
        "alone in the forest survival",
        "winter survival shelter",
        "wilderness survival skills",
        "off grid bushcraft camp",
        "solo camping forest",
        "survival shelter overnight",
        "primitive cooking outdoor",
        "extreme cold survival",
    ],
    "trap": [
        "fish trap traditional",
        "primitive fish trap catch",
        "underwater fish trap",
        "village fishing trap",
        "ancient fish trap method",
    ],
    "camping": [
        "solo camping nature",
        "rainy night camping shelter",
        "asmr camping cook outdoor",
        "winter camping snow tent",
        "primitive camping wilderness",
    ],
}


def expand_with_preset(preset: str) -> list[str]:
    """Return the canonical query list for a niche preset. Unknown -> empty."""
    return list(NICHE_PRESETS.get(preset.lower().strip(), []))


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


def _load_cache() -> dict[str, Any]:
    if not os.path.exists(_CACHE_PATH):
        return {}
    try:
        with open(_CACHE_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return {}
        return data
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
        tmp = _CACHE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cache, fh)
        os.replace(tmp, _CACHE_PATH)
    except OSError:
        # Cache failures must never break discovery.
        pass


def _cache_key(query: str, per_query: int) -> str:
    return f"{query.lower()}::{per_query}"


def _cache_get(query: str, per_query: int) -> list[dict[str, Any]] | None:
    cache = _load_cache()
    entry = cache.get(_cache_key(query, per_query))
    if not entry:
        return None
    ts = entry.get("ts", 0)
    if time.time() - ts > _CACHE_TTL_S:
        return None
    entries = entry.get("entries")
    if not isinstance(entries, list):
        return None
    return entries


def _cache_put(query: str, per_query: int, entries: list[dict[str, Any]]) -> None:
    cache = _load_cache()
    cache[_cache_key(query, per_query)] = {"ts": time.time(), "entries": entries}
    _save_cache(cache)


# ---------------------------------------------------------------------------
# yt-dlp wrappers
# ---------------------------------------------------------------------------


def _ydl_search(query: str, per_query: int) -> list[dict[str, Any]]:
    """Run a single `ytsearch{N}:{query}` and return raw entry dicts.

    Uses `extract_flat=True` so we get one HTTP round-trip per query instead
    of one per video. Returns an empty list on any yt-dlp error; callers
    should be resilient to flaky network conditions.
    """
    if yt_dlp is None:
        return []
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"ytsearch{per_query}:{query}", download=False)
    except Exception:
        return []
    if not info:
        return []
    entries = info.get("entries") or []
    return [e for e in entries if isinstance(e, dict)]


def _ydl_lookup_upload_date(video_id: str) -> str:
    """Fetch the upload_date for a single video via a full (slow) extract.

    Returns "" if anything goes wrong; the ranker treats an unknown date as
    "old" (no recency boost), which is the safe default.
    """
    if yt_dlp is None:
        return ""
    opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(
                f"https://www.youtube.com/watch?v={video_id}", download=False
            )
    except Exception:
        return ""
    if not info:
        return ""
    return str(info.get("upload_date") or "")


def _entry_to_candidate(entry: dict[str, Any]) -> VideoCandidate | None:
    """Coerce a yt-dlp entry dict into a `VideoCandidate`.

    Drops entries that are missing the bare-minimum identifiers we need
    downstream (id + title). Defaults sensibly for everything else; the
    filter stage will weed out videos that look unusable.
    """
    vid = entry.get("id")
    title = entry.get("title")
    if not vid or not title:
        return None
    url = entry.get("url") or entry.get("webpage_url") or f"https://www.youtube.com/watch?v={vid}"
    # extract_flat gives us search-page urls; normalize to canonical watch URL.
    if "youtube.com/watch" not in url and "youtu.be/" not in url:
        url = f"https://www.youtube.com/watch?v={vid}"
    views = entry.get("view_count") or 0
    try:
        views_int = int(views)
    except (TypeError, ValueError):
        views_int = 0
    duration = entry.get("duration") or 0
    try:
        duration_f = float(duration)
    except (TypeError, ValueError):
        duration_f = 0.0
    channel = entry.get("channel") or entry.get("uploader") or ""
    channel_id = entry.get("channel_id") or entry.get("uploader_id") or ""
    upload_date = str(entry.get("upload_date") or "")
    description = str(entry.get("description") or "")
    return VideoCandidate(
        video_id=str(vid),
        url=str(url),
        title=str(title),
        channel=str(channel),
        channel_id=str(channel_id),
        views=views_int,
        duration_s=duration_f,
        upload_date=upload_date,
        description=description,
    )


# ---------------------------------------------------------------------------
# Public: search
# ---------------------------------------------------------------------------


def search_videos(
    topic: str,
    *,
    queries: list[str] | None = None,
    per_query: int = 20,
    max_results: int = 30,
) -> list[VideoCandidate]:
    """Run yt-dlp `ytsearch` across query variants and return deduped candidates.

    `queries` overrides the auto-expansion of `topic` when provided. Results
    are deduped by `video_id` and capped at `max_results` (highest views win
    when truncating, which is the same axis the ranker cares about most).
    """
    qs = queries if queries is not None else _expand_queries(topic)
    if not qs:
        return []

    by_id: dict[str, VideoCandidate] = {}
    for q in qs:
        cached = _cache_get(q, per_query)
        if cached is not None:
            entries = cached
        else:
            entries = _ydl_search(q, per_query)
            # Only cache non-empty results so a transient failure does not
            # poison the cache for 6 hours.
            if entries:
                _cache_put(q, per_query, entries)
        for entry in entries:
            cand = _entry_to_candidate(entry)
            if cand is None:
                continue
            existing = by_id.get(cand.video_id)
            if existing is None or cand.views > existing.views:
                # Prefer the entry with the higher view count; later queries
                # sometimes return the same video with richer fields.
                by_id[cand.video_id] = cand

    cands = list(by_id.values())
    # Pre-sort by views so truncation keeps the most popular ones.
    cands.sort(key=lambda c: c.views, reverse=True)
    return cands[:max_results]


# ---------------------------------------------------------------------------
# Language / age helpers
# ---------------------------------------------------------------------------

# Vietnamese diacritics that aren't part of standard ASCII Latin.
_VI_DIACRITICS = set("ăâđêôơưĂÂĐÊÔƠƯáàảãạắằẳẵặấầẩẫậéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ")


def _looks_english(text: str) -> bool:
    """Heuristic: a title is "English" if it has no CJK chars and no
    Vietnamese diacritics. Good enough for the en-vs-vi split this module
    promises to make.
    """
    if not text:
        return True
    for ch in text:
        # CJK / Hiragana / Katakana / Hangul ranges.
        code = ord(ch)
        if 0x3040 <= code <= 0x9FFF or 0xAC00 <= code <= 0xD7AF:
            return False
        if ch in _VI_DIACRITICS:
            return False
        # Block tones via combining marks (used in NFD-decomposed Vietnamese).
        if unicodedata.category(ch) == "Mn":
            base = unicodedata.normalize("NFD", ch)
            if any(0x0300 <= ord(c) <= 0x036F for c in base):
                return False
    return True


def _age_days(upload_date: str, now: datetime | None = None) -> int | None:
    """Return age in days for a `YYYYMMDD` string, or `None` if unparseable."""
    if not upload_date or len(upload_date) < 8:
        return None
    try:
        dt = datetime.strptime(upload_date[:8], "%Y%m%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    ref = now or datetime.now(timezone.utc)
    delta = ref - dt
    return max(0, int(delta.total_seconds() // 86400))


# ---------------------------------------------------------------------------
# Public: filter
# ---------------------------------------------------------------------------


def filter_candidates(
    cands: Iterable[VideoCandidate],
    *,
    min_views: int = 50_000,
    min_duration_s: float = 180.0,
    max_duration_s: float = 3600.0,
    max_age_days: int | None = None,
    blocked_channels: set[str] | None = None,
    require_english: bool = True,
) -> list[VideoCandidate]:
    """Keep only candidates that pass every gate.

    Shorts (duration < 60s) are dropped unconditionally, regardless of the
    `min_duration_s` argument, because the downstream pipeline is designed
    around long-form sources.
    """
    blocked = blocked_channels or set()
    out: list[VideoCandidate] = []
    for c in cands:
        if c.views < min_views:
            continue
        if c.duration_s and c.duration_s < _SHORT_THRESHOLD_S:
            continue  # Shorts
        if c.duration_s and c.duration_s < min_duration_s:
            continue
        if c.duration_s and c.duration_s > max_duration_s:
            continue
        if c.channel in blocked or c.channel_id in blocked:
            continue
        if require_english and not _looks_english(c.title):
            continue
        if max_age_days is not None:
            age = _age_days(c.upload_date)
            # If we don't know the upload date yet, keep it; the second-pass
            # lookup in `discover()` will fill it in for the survivors.
            if age is not None and age > max_age_days:
                continue
        out.append(c)
    return out


# ---------------------------------------------------------------------------
# Public: rank
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", text.lower()) if t}


def _score(cand: VideoCandidate, topic_tokens: set[str]) -> tuple[float, list[str]]:
    """Apply the scoring heuristic from the spec and collect human reasons."""
    reasons: list[str] = []
    score = 0.0

    if cand.views > 0:
        view_score = math.log10(max(cand.views, 1)) * 10.0
        score += view_score
        if cand.views >= 1_000_000:
            reasons.append(f"viral views ({cand.views:,})")
        elif cand.views >= 100_000:
            reasons.append(f"high views ({cand.views:,})")
        else:
            reasons.append(f"views={cand.views:,}")

    age = _age_days(cand.upload_date)
    if age is not None and age < 90:
        score += (90 - age) * 0.1
        reasons.append(f"recent ({age}d old)")

    if 300.0 <= cand.duration_s <= 1500.0:  # 5-25 min sweet spot
        score += 1.0
        reasons.append("ideal length (5-25m)")
    else:
        score += 0.3

    if topic_tokens:
        title_tokens = _tokenize(cand.title)
        if topic_tokens & title_tokens:
            score += 5.0
            reasons.append("topic match in title")

    return score, reasons


def rank_candidates(
    cands: Iterable[VideoCandidate], *, topic: str | None = None
) -> list[VideoCandidate]:
    """Score every candidate and return them sorted best-first.

    `topic` is optional; passing it enables the title-match bonus. Each
    candidate's `score` and `reasons` are mutated in place so downstream
    consumers can show explanations alongside the ranking.
    """
    topic_tokens = _tokenize(topic) if topic else set()
    ranked: list[VideoCandidate] = []
    for c in cands:
        score, reasons = _score(c, topic_tokens)
        c.score = round(score, 3)
        c.reasons = reasons
        ranked.append(c)
    ranked.sort(key=lambda c: c.score, reverse=True)
    return ranked


# ---------------------------------------------------------------------------
# Public: end-to-end
# ---------------------------------------------------------------------------


def _hydrate_upload_dates(cands: list[VideoCandidate], limit: int) -> None:
    """For the top `limit` candidates without an upload_date, do a slow
    per-video lookup so the recency boost / age filter has real data to work
    with. Limited to keep wall-time bounded.
    """
    hydrated = 0
    for c in cands:
        if hydrated >= limit:
            break
        if c.upload_date:
            continue
        date = _ydl_lookup_upload_date(c.video_id)
        if date:
            c.upload_date = date
        hydrated += 1


def discover(
    topic: str,
    *,
    top: int = 5,
    min_views: int = 50_000,
    max_age_days: int | None = 180,
    per_query: int = 20,
    max_results: int = 30,
    blocked_channels: set[str] | None = None,
    require_english: bool = True,
    queries: list[str] | None = None,
) -> list[VideoCandidate]:
    """Search -> filter -> rank -> return top N.

    Two-pass: first a fast flat search gives us views/duration/title for
    deduping and filtering, then a slow per-video lookup hydrates
    `upload_date` on the survivors so recency-based scoring is meaningful.

    ``queries`` lets callers (e.g. niche presets) pass a curated list of
    search terms instead of relying on the default auto-expansion of
    ``topic``. When set, the ranker still uses ``topic`` for keyword bonus.
    """
    raw = search_videos(
        topic, queries=queries, per_query=per_query, max_results=max_results
    )

    # First filter pass: no age filter yet (upload_date is missing for most).
    survivors = filter_candidates(
        raw,
        min_views=min_views,
        min_duration_s=180.0,
        max_duration_s=3600.0,
        max_age_days=None,
        blocked_channels=blocked_channels,
        require_english=require_english,
    )

    # Cap hydration so a topic with many results doesn't fan out to dozens
    # of slow lookups. 2x top gives the ranker room to demote a few.
    _hydrate_upload_dates(survivors, limit=max(top * 2, 10))

    # Second filter pass: now apply the age gate using the new upload dates.
    if max_age_days is not None:
        survivors = filter_candidates(
            survivors,
            min_views=min_views,
            min_duration_s=180.0,
            max_duration_s=3600.0,
            max_age_days=max_age_days,
            blocked_channels=blocked_channels,
            require_english=require_english,
        )

    ranked = rank_candidates(survivors, topic=topic)
    return ranked[:top]

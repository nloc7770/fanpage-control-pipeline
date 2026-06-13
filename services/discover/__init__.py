"""Video discovery service.

Crawls YouTube via yt-dlp for candidate videos matching a topic, then filters
and ranks them so the existing pipeline can ingest the best ones.
"""

from services.discover.runner import (
    VideoCandidate,
    discover,
    filter_candidates,
    rank_candidates,
    search_videos,
)

__all__ = [
    "VideoCandidate",
    "discover",
    "filter_candidates",
    "rank_candidates",
    "search_videos",
]

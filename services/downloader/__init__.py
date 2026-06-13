"""Source-video downloader (yt-dlp wrapper)."""

from __future__ import annotations

from services.downloader.ytdlp_runner import DownloadResult, download

__all__ = ["DownloadResult", "download"]

"""Rendering runner: persists progress and honors MOCK_RENDER."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass(slots=True)
class RenderResult:
    output_path: Path
    duration_s: float
    ffmpeg_command: str | None = None


def render(
    plan: Any,
    source_path: str | Path,
    output_path: str | Path,
    *,
    start_time: float,
    end_time: float,
    subtitle_lines: list[dict[str, Any]] | None = None,
    transcript_words: list[dict[str, Any]] | None = None,
    highlight_segments: list[dict[str, Any]] | None = None,
    progress_cb: Callable[[float], None] | None = None,
) -> RenderResult:
    """Render one clip. Honors ``MOCK_RENDER``.

    Returns the final ``output_path``, the realised duration, and (when
    available) the canonical ffmpeg command used to produce it so the worker
    can stash it on ``render_tasks.ffmpeg_command`` for debugging.

    ``transcript_words`` is a flat list of ``{"word", "start", "end",
    "speaker"?}`` dicts in source-video time; the pipeline picks the words
    overlapping the clip window, re-maps them through any silence-tighten
    intervals, and burns them in as karaoke subtitles.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if os.environ.get("MOCK_RENDER", "0") == "1":
        return _mock_render(output_path, end_time - start_time, progress_cb)

    # Defer heavy import until we actually need it.
    from ffmpeg.pipeline import render_clip as _render_clip
    from ffmpeg.probe import get_duration_s

    _render_clip(
        plan=plan,
        source_path=source_path,
        output_path=output_path,
        progress_cb=progress_cb,
        start_time=start_time,
        end_time=end_time,
        subtitle_lines=subtitle_lines,
        transcript_words=transcript_words,
        highlight_segments=highlight_segments,
    )
    duration = get_duration_s(output_path)
    return RenderResult(output_path=output_path, duration_s=duration, ffmpeg_command=None)


def _mock_render(
    output_path: Path,
    duration_s: float,
    progress_cb: Callable[[float], None] | None,
) -> RenderResult:
    """Skip ffmpeg entirely when MOCK_RENDER is on.

    We try ``write_blank_mp4`` first so test fixtures resemble real output, but
    fall back to a small stub file if ffmpeg isn't available -- the rest of
    the test suite only checks for non-zero size + that the row was updated.
    """
    logger.info("MOCK_RENDER: writing stub to {} (target dur={}s)", output_path, duration_s)
    target_dur = max(0.5, min(duration_s, 5.0))
    try:
        from ffmpeg.pipeline import write_blank_mp4

        if shutil.which("ffmpeg"):
            write_blank_mp4(output_path, duration_s=target_dur)
        else:
            raise FileNotFoundError("ffmpeg binary not on PATH")
    except Exception as exc:
        logger.warning(
            "MOCK_RENDER: ffmpeg unavailable ({}); writing zero-byte sentinel", exc
        )
        # Smallest possible MP4-shaped sentinel: enough for ``size > 0`` checks.
        output_path.write_bytes(b"\x00\x00\x00\x20ftypisom" + b"\x00" * 8)

    if progress_cb:
        progress_cb(50.0)
        progress_cb(100.0)

    return RenderResult(
        output_path=output_path,
        duration_s=target_dur,
        ffmpeg_command="MOCK_RENDER=1 (stubbed)",
    )

"""Tests for the recap-montage cut + word-remap helpers.

Covers:

* :func:`ffmpeg.cut.cut_concat` -- given N source intervals, the output mp4
  duration equals the sum of the intervals (ffmpeg may vary by a few hundred
  milliseconds due to re-encode quantisation, so we allow a small epsilon).
* :func:`ffmpeg.pipeline.remap_word_time_through_highlights` -- the
  piecewise source-time -> stitched-time map used by ``_clip_words`` when
  highlight_segments are present.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.conftest import requires_ffmpeg

from ffmpeg.cut import cut_concat
from ffmpeg.pipeline import remap_word_time_through_highlights
from ffmpeg.probe import get_duration_s


def _ffmpeg() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def _make_sine_video(path: Path, duration_s: float, freq: int = 440) -> Path:
    """Synthesise a small video with a sine-tone audio track for cut tests."""
    cmd = [
        _ffmpeg(), "-y", "-loglevel", "error",
        "-f", "lavfi",
        "-i", f"color=c=blue:s=320x240:r=24:d={duration_s:.3f}",
        "-f", "lavfi",
        "-i", f"sine=frequency={freq}:r=44100:d={duration_s:.3f}",
        "-shortest",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k", "-ar", "44100", "-ac", "2",
        str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return path


# ---------------------------------------------------------------------------
# cut_concat
# ---------------------------------------------------------------------------


@requires_ffmpeg
def test_cut_concat_three_intervals_duration_equals_sum(tmp_path: Path) -> None:
    """Three non-overlapping intervals on a 10s source -> output dur = sum."""
    src = _make_sine_video(tmp_path / "src.mp4", duration_s=10.0)
    out = tmp_path / "concat.mp4"

    intervals = [(1.0, 3.0), (4.5, 6.0), (7.0, 9.5)]  # sum = 6.0s
    cut_concat(src, intervals, out, reencode=True)

    assert out.exists() and out.stat().st_size > 0
    expected = sum(b - a for a, b in intervals)
    actual = get_duration_s(out)
    # Allow ~0.3s slop for keyframe alignment + re-encode boundary effects.
    assert abs(actual - expected) < 0.4, (
        f"expected ~{expected:.2f}s got {actual:.2f}s"
    )


@requires_ffmpeg
def test_cut_concat_single_interval_is_plain_cut(tmp_path: Path) -> None:
    """One-interval input should still produce a valid mp4 of that length."""
    src = _make_sine_video(tmp_path / "src.mp4", duration_s=5.0)
    out = tmp_path / "one.mp4"

    cut_concat(src, [(1.0, 3.0)], out, reencode=True)

    assert out.exists() and out.stat().st_size > 0
    assert abs(get_duration_s(out) - 2.0) < 0.3


def test_cut_concat_empty_intervals_raises(tmp_path: Path) -> None:
    """Degenerate inputs should fail loudly so callers don't ship empty mp4s."""
    with pytest.raises(ValueError):
        cut_concat("/dev/null", [], tmp_path / "out.mp4")
    with pytest.raises(ValueError):
        cut_concat("/dev/null", [(5.0, 5.0)], tmp_path / "out.mp4")


# ---------------------------------------------------------------------------
# remap_word_time_through_highlights
# ---------------------------------------------------------------------------


def test_remap_word_time_inside_first_highlight() -> None:
    """A word inside hl1 maps to its offset from hl1.start."""
    highlights = [
        {"start": 100.0, "end": 110.0},
        {"start": 200.0, "end": 215.0},
    ]
    # Inside hl1: 105.0 -> 5.0 (offset 0, t - hl.start = 5).
    assert remap_word_time_through_highlights(105.0, highlights) == pytest.approx(5.0)


def test_remap_word_time_inside_second_highlight() -> None:
    """A word inside hl2 includes hl1's duration as offset."""
    highlights = [
        {"start": 100.0, "end": 110.0},  # 10s
        {"start": 200.0, "end": 215.0},  # 15s
    ]
    # Inside hl2: 205.0 -> 10.0 + (205 - 200) = 15.0.
    assert remap_word_time_through_highlights(205.0, highlights) == pytest.approx(15.0)


def test_remap_word_time_in_gap_returns_none() -> None:
    """A timestamp between two highlights has no stitched-time analogue."""
    highlights = [
        {"start": 100.0, "end": 110.0},
        {"start": 200.0, "end": 215.0},
    ]
    assert remap_word_time_through_highlights(150.0, highlights) is None


def test_remap_word_time_before_all_returns_none() -> None:
    """Source times preceding the first highlight are not in the recap."""
    highlights = [{"start": 100.0, "end": 110.0}]
    assert remap_word_time_through_highlights(50.0, highlights) is None


def test_remap_word_time_after_all_returns_none() -> None:
    """Source times after the last highlight are not in the recap."""
    highlights = [{"start": 100.0, "end": 110.0}]
    assert remap_word_time_through_highlights(200.0, highlights) is None


def test_remap_word_time_on_boundaries() -> None:
    """The boundary moments map inclusively so edge-of-highlight words survive."""
    highlights = [
        {"start": 100.0, "end": 110.0},
        {"start": 200.0, "end": 215.0},
    ]
    # hl1.start.
    assert remap_word_time_through_highlights(100.0, highlights) == pytest.approx(0.0)
    # hl1.end (boundary -> the first highlight wins).
    assert remap_word_time_through_highlights(110.0, highlights) == pytest.approx(10.0)
    # hl2.start.
    assert remap_word_time_through_highlights(200.0, highlights) == pytest.approx(10.0)
    # hl2.end.
    assert remap_word_time_through_highlights(215.0, highlights) == pytest.approx(25.0)


def test_remap_word_time_empty_highlights_returns_none() -> None:
    """No highlights -> nothing to map to."""
    assert remap_word_time_through_highlights(1.0, []) is None

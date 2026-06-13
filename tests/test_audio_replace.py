"""Unit tests for ``replace_audio`` and ``mute_audio``.

Both use tiny ffmpeg-synthesised inputs (``lavfi`` colour + sine) so the tests
don't depend on any fixture binaries -- just on ffmpeg/ffprobe being on PATH.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.conftest import requires_ffmpeg

from ffmpeg.audio import mute_audio, replace_audio
from ffmpeg.probe import ffprobe_json, get_duration_s


def _ffmpeg() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def _make_video(path: Path, duration_s: float) -> Path:
    """Synthesise a tiny silent 320x240 mp4 at the given duration."""
    cmd = [
        _ffmpeg(), "-y", "-loglevel", "error",
        "-f", "lavfi",
        "-i", f"color=c=blue:s=320x240:r=24:d={duration_s:.3f}",
        "-f", "lavfi",
        "-i", f"sine=frequency=440:r=44100:d={duration_s:.3f}",
        "-shortest",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "64k",
        str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return path


def _make_wav(path: Path, duration_s: float, freq: int = 880) -> Path:
    """Synthesise a sine-tone WAV at the given duration."""
    cmd = [
        _ffmpeg(), "-y", "-loglevel", "error",
        "-f", "lavfi",
        "-i", f"sine=frequency={freq}:r=44100:d={duration_s:.3f}",
        "-ac", "2",
        str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return path


def _audio_codec(path: Path) -> str:
    info = ffprobe_json(path)
    for s in info.get("streams", []) or []:
        if s.get("codec_type") == "audio":
            return str(s.get("codec_name"))
    return ""


def _video_codec(path: Path) -> str:
    info = ffprobe_json(path)
    for s in info.get("streams", []) or []:
        if s.get("codec_type") == "video":
            return str(s.get("codec_name"))
    return ""


def _sample_rate(path: Path) -> int:
    info = ffprobe_json(path)
    for s in info.get("streams", []) or []:
        if s.get("codec_type") == "audio":
            try:
                return int(s.get("sample_rate", 0))
            except (TypeError, ValueError):
                return 0
    return 0


def _channels(path: Path) -> int:
    info = ffprobe_json(path)
    for s in info.get("streams", []) or []:
        if s.get("codec_type") == "audio":
            try:
                return int(s.get("channels", 0))
            except (TypeError, ValueError):
                return 0
    return 0


@requires_ffmpeg
def test_replace_audio_fit_to_video_pads_short_tts(tmp_path: Path) -> None:
    """A TTS clip shorter than the video should be padded with silence."""
    video = _make_video(tmp_path / "in.mp4", duration_s=4.0)
    tts = _make_wav(tmp_path / "tts.wav", duration_s=1.5)
    out = tmp_path / "out.mp4"

    replace_audio(video, tts, out, fit_to="video")

    assert out.exists() and out.stat().st_size > 0
    out_dur = get_duration_s(out)
    # Should match the video's duration (within ffmpeg rounding).
    assert abs(out_dur - 4.0) < 0.3, f"expected ~4.0s, got {out_dur}"

    assert _audio_codec(out) == "aac"
    assert _sample_rate(out) == 44100
    assert _channels(out) == 2


@requires_ffmpeg
def test_replace_audio_fit_to_video_speeds_up_long_tts(tmp_path: Path) -> None:
    """A TTS clip longer than the video should be sped up (atempo capped at 1.10)
    and trimmed so the output duration matches the video."""
    video = _make_video(tmp_path / "in.mp4", duration_s=2.0)
    tts = _make_wav(tmp_path / "tts.wav", duration_s=4.0)  # 2x too long
    out = tmp_path / "out.mp4"

    replace_audio(video, tts, out, fit_to="video")

    assert out.exists() and out.stat().st_size > 0
    out_dur = get_duration_s(out)
    # Capped atempo (1.10) cannot fully fit a 2x overshoot, but the apad+atrim
    # and -shortest guards must clamp the output to the video duration.
    assert abs(out_dur - 2.0) < 0.3, f"expected ~2.0s, got {out_dur}"
    assert _audio_codec(out) == "aac"


@requires_ffmpeg
def test_replace_audio_fit_to_audio_slows_video(tmp_path: Path) -> None:
    """fit_to='audio' should stretch the video to match the audio's duration."""
    video = _make_video(tmp_path / "in.mp4", duration_s=2.0)
    tts = _make_wav(tmp_path / "tts.wav", duration_s=3.0)
    out = tmp_path / "out.mp4"

    replace_audio(video, tts, out, fit_to="audio")

    out_dur = get_duration_s(out)
    # Output duration tracks the audio (3.0s) since we stretched the video.
    assert abs(out_dur - 3.0) < 0.4, f"expected ~3.0s, got {out_dur}"
    assert _audio_codec(out) == "aac"


@requires_ffmpeg
def test_replace_audio_rejects_missing_inputs(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        replace_audio(tmp_path / "nope.mp4", tmp_path / "x.wav", tmp_path / "out.mp4")


@requires_ffmpeg
def test_replace_audio_rejects_bad_fit_to(tmp_path: Path) -> None:
    video = _make_video(tmp_path / "in.mp4", duration_s=1.0)
    tts = _make_wav(tmp_path / "tts.wav", duration_s=1.0)
    with pytest.raises(ValueError):
        replace_audio(video, tts, tmp_path / "out.mp4", fit_to="bogus")


@requires_ffmpeg
def test_mute_audio_produces_silent_track(tmp_path: Path) -> None:
    """``mute_audio`` should leave video intact and emit a silent AAC track."""
    video = _make_video(tmp_path / "in.mp4", duration_s=2.0)
    out = tmp_path / "muted.mp4"

    mute_audio(video, out)

    assert out.exists() and out.stat().st_size > 0
    out_dur = get_duration_s(out)
    assert abs(out_dur - 2.0) < 0.3
    assert _audio_codec(out) == "aac"
    # video should be stream-copied (h264, same as input)
    assert _video_codec(out) == _video_codec(video)


@requires_ffmpeg
def test_mute_audio_rejects_missing_input(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        mute_audio(tmp_path / "nope.mp4", tmp_path / "out.mp4")

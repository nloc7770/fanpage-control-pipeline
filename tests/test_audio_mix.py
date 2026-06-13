"""Tests for the background-preserving voiceover splice.

These cover:

* :func:`extract_audio` -- video -> stereo wav.
* :func:`mix_voiceover_with_background` -- two wavs in, one stereo wav out
  with non-silent content.
* :func:`replace_audio_keep_background` -- end-to-end mp4 with non-silent
  audio when ``separate_vocals`` is monkey-patched to return predefined
  wavs (so we don't need a real Demucs model on the test runner).
* The fallback path (``use_demucs=False``) which exercises the ffmpeg
  center-channel cancel filter.

We deliberately do not call real Demucs here -- it would tie the test suite
to an 80 MB model download and a GPU. The integration smoke test for Demucs
happens at the worker level.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.conftest import requires_ffmpeg

from ffmpeg import audio as audio_mod
from ffmpeg.audio import (
    extract_audio,
    mix_voiceover_with_background,
    replace_audio_keep_background,
    separate_vocals,
)
from ffmpeg.probe import ffprobe_json, get_duration_s


def _ffmpeg() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def _make_stereo_video(path: Path, duration_s: float, music_freq: int = 220) -> Path:
    """Synthesise a small stereo video with a sine-tone "music" bed."""
    cmd = [
        _ffmpeg(), "-y", "-loglevel", "error",
        "-f", "lavfi",
        "-i", f"color=c=red:s=320x240:r=24:d={duration_s:.3f}",
        "-f", "lavfi",
        "-i",
        f"sine=frequency={music_freq}:r=44100:d={duration_s:.3f}",
        "-shortest",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k", "-ar", "44100", "-ac", "2",
        str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return path


def _make_wav(path: Path, duration_s: float, freq: int = 880) -> Path:
    cmd = [
        _ffmpeg(), "-y", "-loglevel", "error",
        "-f", "lavfi",
        "-i", f"sine=frequency={freq}:r=44100:d={duration_s:.3f}",
        "-ac", "2",
        str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return path


def _mean_volume_db(path: Path) -> float:
    """Probe mean_volume via ffmpeg's volumedetect filter."""
    proc = subprocess.run(
        [
            _ffmpeg(), "-i", str(path),
            "-af", "volumedetect",
            "-f", "null", "-",
        ],
        capture_output=True, text=True, check=False,
    )
    blob = (proc.stderr or "") + (proc.stdout or "")
    for line in blob.splitlines():
        if "mean_volume:" in line:
            try:
                token = line.split("mean_volume:")[1].strip().split()[0]
                return float(token)
            except (IndexError, ValueError):
                continue
    return float("nan")


def _audio_channels(path: Path) -> int:
    info = ffprobe_json(path)
    for s in info.get("streams", []) or []:
        if s.get("codec_type") == "audio":
            try:
                return int(s.get("channels", 0))
            except (TypeError, ValueError):
                return 0
    return 0


def _sample_rate(path: Path) -> int:
    info = ffprobe_json(path)
    for s in info.get("streams", []) or []:
        if s.get("codec_type") == "audio":
            try:
                return int(s.get("sample_rate", 0))
            except (TypeError, ValueError):
                return 0
    return 0


# ---------------------------------------------------------------------------
# extract_audio
# ---------------------------------------------------------------------------


@requires_ffmpeg
def test_extract_audio_produces_stereo_wav(tmp_path: Path) -> None:
    video = _make_stereo_video(tmp_path / "in.mp4", duration_s=2.0)
    wav = tmp_path / "out.wav"

    extract_audio(video, wav)

    assert wav.exists() and wav.stat().st_size > 0
    assert _audio_channels(wav) == 2
    assert _sample_rate(wav) == 44100
    # Duration should be ~2.0s.
    assert abs(get_duration_s(wav) - 2.0) < 0.3


# ---------------------------------------------------------------------------
# mix_voiceover_with_background
# ---------------------------------------------------------------------------


@requires_ffmpeg
def test_mix_voiceover_with_background_no_duck(tmp_path: Path) -> None:
    instr = _make_wav(tmp_path / "instr.wav", duration_s=3.0, freq=220)
    vo = _make_wav(tmp_path / "vo.wav", duration_s=3.0, freq=880)
    out = tmp_path / "mix.wav"

    mix_voiceover_with_background(
        instr, vo, out, bg_gain_db=-6.0, vo_gain_db=0.0, duck_bg=False
    )

    assert out.exists() and out.stat().st_size > 0
    assert _audio_channels(out) == 2
    assert _sample_rate(out) == 44100
    # Mix should be present (not silent).
    mv = _mean_volume_db(out)
    assert mv > -60.0, f"mix appears silent (mean_volume={mv} dB)"


@requires_ffmpeg
def test_mix_voiceover_with_background_with_duck(tmp_path: Path) -> None:
    instr = _make_wav(tmp_path / "instr.wav", duration_s=2.5, freq=220)
    vo = _make_wav(tmp_path / "vo.wav", duration_s=2.5, freq=880)
    out = tmp_path / "mix.wav"

    mix_voiceover_with_background(
        instr, vo, out, bg_gain_db=-8.0, vo_gain_db=0.0, duck_bg=True
    )

    assert out.exists() and out.stat().st_size > 0
    assert _audio_channels(out) == 2
    mv = _mean_volume_db(out)
    assert mv > -60.0, f"ducked mix is silent (mean_volume={mv} dB)"


@requires_ffmpeg
def test_mix_voiceover_duration_is_longest(tmp_path: Path) -> None:
    """When TTS is shorter than background, the mix should keep the bg tail."""
    instr = _make_wav(tmp_path / "instr.wav", duration_s=4.0, freq=220)
    vo = _make_wav(tmp_path / "vo.wav", duration_s=1.5, freq=880)
    out = tmp_path / "mix.wav"

    mix_voiceover_with_background(instr, vo, out, duck_bg=False)

    out_dur = get_duration_s(out)
    # Mix duration tracks the longest input (~4.0s).
    assert out_dur > 3.5, f"expected ~4.0s mix, got {out_dur}"


# ---------------------------------------------------------------------------
# separate_vocals (ffmpeg fallback)
# ---------------------------------------------------------------------------


@requires_ffmpeg
def test_separate_vocals_ffmpeg_fallback(tmp_path: Path) -> None:
    """The center-cancel fallback must always produce both stems."""
    src = _make_wav(tmp_path / "src.wav", duration_s=2.0, freq=440)
    out_dir = tmp_path / "sep"

    voc, instr = separate_vocals(src, out_dir, use_demucs=False)

    assert voc.exists() and voc.stat().st_size > 0
    assert instr.exists() and instr.stat().st_size > 0


# ---------------------------------------------------------------------------
# replace_audio_keep_background (end-to-end with mocked separation)
# ---------------------------------------------------------------------------


@requires_ffmpeg
def test_replace_audio_keep_background_with_mock_sep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: video + TTS -> mp4, with separate_vocals stubbed.

    We patch :func:`separate_vocals` to return two predetermined wavs (so the
    test doesn't require a real Demucs model). The output mp4 must contain a
    non-silent audio track with both signals mixed in.
    """
    video = _make_stereo_video(tmp_path / "src.mp4", duration_s=3.0, music_freq=220)
    tts = _make_wav(tmp_path / "tts.wav", duration_s=3.0, freq=880)
    out_mp4 = tmp_path / "out.mp4"

    # Pre-make the "separated" stems.
    fake_voc = _make_wav(tmp_path / "fake_voc.wav", duration_s=3.0, freq=400)
    fake_instr = _make_wav(tmp_path / "fake_instr.wav", duration_s=3.0, freq=220)

    def _fake_separate(audio_path, output_dir, *, device="cuda", use_demucs=True):
        return fake_voc, fake_instr

    monkeypatch.setattr(audio_mod, "separate_vocals", _fake_separate)

    replace_audio_keep_background(
        video, tts, out_mp4, bg_gain_db=-8.0, use_demucs=True
    )

    assert out_mp4.exists() and out_mp4.stat().st_size > 0
    # Output duration ~ video duration.
    assert abs(get_duration_s(out_mp4) - 3.0) < 0.4
    # Output audio must be non-silent (the mix is in there).
    mv = _mean_volume_db(out_mp4)
    assert mv > -60.0, f"output audio appears silent (mean_volume={mv} dB)"


@requires_ffmpeg
def test_replace_audio_keep_background_falls_back_on_sep_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If separation crashes, we must still produce a valid mp4 (legacy path)."""
    video = _make_stereo_video(tmp_path / "src.mp4", duration_s=2.0)
    tts = _make_wav(tmp_path / "tts.wav", duration_s=2.0, freq=660)
    out_mp4 = tmp_path / "out.mp4"

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated demucs failure")

    monkeypatch.setattr(audio_mod, "separate_vocals", _boom)

    replace_audio_keep_background(video, tts, out_mp4, use_demucs=True)

    assert out_mp4.exists() and out_mp4.stat().st_size > 0
    # Should still be a real, non-silent audio track (it's the TTS alone).
    mv = _mean_volume_db(out_mp4)
    assert mv > -60.0
    assert abs(get_duration_s(out_mp4) - 2.0) < 0.3


@requires_ffmpeg
def test_replace_audio_keep_background_uses_ffmpeg_when_use_demucs_false(
    tmp_path: Path,
) -> None:
    """``use_demucs=False`` exercises the real ffmpeg center-cancel path."""
    video = _make_stereo_video(tmp_path / "src.mp4", duration_s=2.0)
    tts = _make_wav(tmp_path / "tts.wav", duration_s=2.0, freq=660)
    out_mp4 = tmp_path / "out.mp4"

    replace_audio_keep_background(video, tts, out_mp4, use_demucs=False)

    assert out_mp4.exists() and out_mp4.stat().st_size > 0
    assert abs(get_duration_s(out_mp4) - 2.0) < 0.3
    mv = _mean_volume_db(out_mp4)
    assert mv > -60.0

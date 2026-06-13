"""Tests for ``services.tts.runner``.

The runner now talks to an external TTS hub over HTTP. The tests stub the
``_hub_synthesize`` helper so we never need network access -- each "hub call"
writes a 1-second 24 kHz mono silence WAV, exercising the chunk/segment
assembly logic end-to-end without depending on the real F5-TTS backend.
"""

from __future__ import annotations

import struct
import subprocess
import wave
from pathlib import Path
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Stub helpers
# ---------------------------------------------------------------------------


def _write_silence_wav(path: Path, *, duration_s: float, sample_rate: int) -> None:
    n_samples = int(duration_s * sample_rate)
    block_align = 2  # 1 ch * 16 bit / 8
    data_size = n_samples * block_align
    with path.open("wb") as fh:
        fh.write(b"RIFF")
        fh.write(struct.pack("<I", 36 + data_size))
        fh.write(b"WAVE")
        fh.write(b"fmt ")
        fh.write(struct.pack("<I", 16))
        fh.write(struct.pack("<H", 1))
        fh.write(struct.pack("<H", 1))
        fh.write(struct.pack("<I", sample_rate))
        fh.write(struct.pack("<I", sample_rate * block_align))
        fh.write(struct.pack("<H", block_align))
        fh.write(struct.pack("<H", 16))
        fh.write(b"data")
        fh.write(struct.pack("<I", data_size))
        fh.write(b"\x00" * data_size)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_hub(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Replace the live hub call with a recorder that emits 1s silence WAVs.

    Returns the list of calls so tests can assert on chunk count / voice
    routing.
    """
    monkeypatch.setenv("TTS_HUB_URL", "http://127.0.0.1:9090")
    monkeypatch.setenv("TTS_VOICE_VI", "male")

    from services.tts import runner

    # Bypass the liveness probe.
    monkeypatch.setattr(runner, "_HUB_ALIVE", True, raising=False)

    calls: list[dict[str, Any]] = []

    def _fake_hub(text: str, out_path: Path, voice: str | None) -> None:
        calls.append({"text": text, "out_path": str(out_path), "voice": voice})
        _write_silence_wav(out_path, duration_s=1.0, sample_rate=24000)

    monkeypatch.setattr(runner, "_hub_synthesize", _fake_hub)
    return calls


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_synthesize_vi_writes_valid_wav(stub_hub: list[dict[str, Any]], tmp_path: Path) -> None:
    """Single-chunk synthesis produces a 24kHz mono PCM WAV at the requested path."""
    from services.tts.runner import synthesize_vi

    out_path = tmp_path / "out.wav"
    result = synthesize_vi("Xin chào pipeline.", out_path)

    assert result == out_path
    assert out_path.exists()
    assert out_path.stat().st_size > 1000
    assert len(stub_hub) == 1
    assert stub_hub[0]["text"] == "Xin chào pipeline."

    with wave.open(str(out_path), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getframerate() == 24000
        assert wf.getsampwidth() == 2

    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_name,sample_rate,channels",
            "-of",
            "default=noprint_wrappers=1",
            str(out_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "codec_name=pcm_s16le" in probe.stdout
    assert "sample_rate=24000" in probe.stdout
    assert "channels=1" in probe.stdout


def test_synthesize_vi_segments_assembles_timed_track(
    stub_hub: list[dict[str, Any]], tmp_path: Path
) -> None:
    """synthesize_vi_segments produces a WAV whose duration matches the clip."""
    from services.tts.runner import synthesize_vi_segments

    segments = [
        {"start": 0.0, "end": 3.0, "text_vi": "Xin chào."},
        {"start": 5.0, "end": 9.5, "text_vi": "Đây là đoạn thứ hai dài hơn."},
        {"start": 9.5, "end": 15.0, "text_vi": "Đoạn cuối khép lại video."},
    ]
    out_path = tmp_path / "segments.wav"

    result = synthesize_vi_segments(segments, clip_duration_s=15.0, output_path=out_path)

    assert result == out_path
    assert out_path.exists()
    assert out_path.stat().st_size > 1000
    assert len(stub_hub) == 3

    with wave.open(str(out_path), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getframerate() == 24000
        dur = wf.getnframes() / wf.getframerate()
        assert 14.8 < dur < 15.2, f"expected ~15s, got {dur:.3f}s"


def test_synthesize_vi_segments_skips_empty_segments(
    stub_hub: list[dict[str, Any]], tmp_path: Path
) -> None:
    """Segments missing text_vi are dropped; remaining slots still fill the clip."""
    from services.tts.runner import synthesize_vi_segments

    segments = [
        {"start": 0.0, "end": 2.0, "text_vi": ""},  # dropped
        {"start": 2.0, "end": 5.0, "text_vi": "Có nội dung ở đây."},
    ]
    out_path = tmp_path / "partial.wav"

    synthesize_vi_segments(segments, clip_duration_s=5.0, output_path=out_path)
    assert len(stub_hub) == 1
    with wave.open(str(out_path), "rb") as wf:
        dur = wf.getnframes() / wf.getframerate()
        assert 4.8 < dur < 5.2


def test_synthesize_vi_hub_unreachable_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If the hub is unreachable, the first call raises TTSError with a hint."""
    from services.tts import runner
    from services.tts.runner import synthesize_vi, TTSError

    # Force the alive cache off so the probe runs.
    monkeypatch.setattr(runner, "_HUB_ALIVE", None, raising=False)
    # Point at a dead port.
    monkeypatch.setenv("TTS_HUB_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("TTS_HUB_TIMEOUT_S", "1")

    with pytest.raises(TTSError) as excinfo:
        synthesize_vi("Xin chào.", tmp_path / "out.wav")
    assert "hub unreachable" in str(excinfo.value).lower()

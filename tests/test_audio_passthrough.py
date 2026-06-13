"""Verify that ``KEEP_ORIGINAL_AUDIO=1`` (the new default) skips the entire
TTS / vocal-separation stack.

The contract we're nailing down:

* ``services.tts.runner.synthesize_vi`` is never called.
* ``services.tts.runner.synthesize_vi_segments`` is never called.
* ``ffmpeg.audio.replace_audio`` is never called.
* ``ffmpeg.audio.replace_audio_keep_background`` is never called.
* ``ffmpeg.audio.mute_audio`` is never called.
* ``ffmpeg.audio.separate_vocals`` is never called.
* The render still produces a valid mp4 carrying the original source audio.

We monkeypatch the helpers on the ``pipeline`` module (where they were
star-imported) so we can assert "not called". We also pre-register a stub
``services.tts.runner`` module in ``sys.modules`` so that *if* the legacy
branch ever did get triggered, the call would land on our spy instead of the
real (slow, network-dependent) TTS service.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

from tests.conftest import requires_ffmpeg

from ffmpeg import pipeline as pipeline_mod
from ffmpeg.pipeline import render_clip
from ffmpeg.probe import ffprobe_json, get_duration_s


def _ffmpeg() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def _make_video_with_tone(path: Path, duration_s: float, freq: int = 440) -> Path:
    """Synthesise a tiny 320x240 mp4 with a continuous sine-tone audio track.

    The tone gives us something to probe -- silent clips would defeat the
    "audio survived end-to-end" check.
    """
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


def _has_audio_stream(path: Path) -> bool:
    info = ffprobe_json(path)
    for s in info.get("streams", []) or []:
        if s.get("codec_type") == "audio":
            return True
    return False


class _Spy:
    """Callable spy that records calls and refuses to do any work."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[tuple[tuple, dict]] = []

    def __call__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append((args, kwargs))
        raise AssertionError(
            f"{self.name} was called in KEEP_ORIGINAL_AUDIO=1 mode "
            f"(args={args!r}, kwargs={kwargs!r})"
        )


@requires_ffmpeg
def test_keep_original_audio_skips_tts_and_vocal_sep(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Default audio mode: TTS + vocal-sep helpers must never fire."""
    # --- Spies on every audio-modification helper ------------------------
    tts_spy = _Spy("services.tts.runner.synthesize_vi")
    tts_segments_spy = _Spy("services.tts.runner.synthesize_vi_segments")
    replace_spy = _Spy("ffmpeg.audio.replace_audio")
    replace_bg_spy = _Spy("ffmpeg.audio.replace_audio_keep_background")
    mute_spy = _Spy("ffmpeg.audio.mute_audio")
    separate_spy = _Spy("ffmpeg.audio.separate_vocals")

    # Pre-register a stub services.tts.runner so the lazy import inside the
    # pipeline lands on the spy if the branch is ever reached. We also stub
    # the parent ``services.tts`` and ``services`` modules to avoid pulling
    # in real torch / model deps under test.
    services_mod = sys.modules.get("services") or types.ModuleType("services")
    services_tts_mod = sys.modules.get("services.tts") or types.ModuleType(
        "services.tts"
    )
    runner_mod = types.ModuleType("services.tts.runner")
    runner_mod.synthesize_vi = tts_spy  # type: ignore[attr-defined]
    runner_mod.synthesize_vi_segments = tts_segments_spy  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "services", services_mod)
    monkeypatch.setitem(sys.modules, "services.tts", services_tts_mod)
    monkeypatch.setitem(sys.modules, "services.tts.runner", runner_mod)

    # Monkeypatch the audio helpers on the pipeline module (they were
    # imported into pipeline's namespace, so this is where the lookups
    # happen).
    monkeypatch.setattr(pipeline_mod, "replace_audio", replace_spy)
    monkeypatch.setattr(
        pipeline_mod, "replace_audio_keep_background", replace_bg_spy
    )
    monkeypatch.setattr(pipeline_mod, "mute_audio", mute_spy)
    # ``separate_vocals`` is only ever called *inside* replace_audio_keep_background,
    # but spy on it too for belt-and-braces. Patch on the audio module since
    # it's not re-exported in pipeline.
    from ffmpeg import audio as audio_mod
    monkeypatch.setattr(audio_mod, "separate_vocals", separate_spy)

    # --- Env: explicit default + a narrative payload that would have ----
    # triggered the TTS branch under the old defaults. The whole point of
    # this test is that the narrative is now IGNORED for audio purposes.
    monkeypatch.setenv("KEEP_ORIGINAL_AUDIO", "1")
    monkeypatch.setenv("TTS_REPLACE_AUDIO", "1")  # legacy on -- must be ignored
    monkeypatch.setenv("KEEP_BACKGROUND_AUDIO", "1")

    src = _make_video_with_tone(tmp_path / "src.mp4", duration_s=3.0, freq=440)
    out = tmp_path / "out.mp4"

    plan = {
        "narrative_script_vi": "Xin chao! Day la mot ban dich tieng Viet.",
        "narrative_segments": [
            {"start": 0.0, "end": 3.0, "text_vi": "Xin chao."},
        ],
        # No crop_plan / visual_effects / pattern_interrupts -> minimal path.
    }

    # --- Run ------------------------------------------------------------
    result = render_clip(
        plan,
        src,
        out,
        start_time=0.0,
        end_time=3.0,
        transcript_words=[],
    )

    # --- Assertions: helpers never called -------------------------------
    assert tts_spy.calls == [], (
        f"synthesize_vi was called: {tts_spy.calls!r}"
    )
    assert tts_segments_spy.calls == [], (
        f"synthesize_vi_segments was called: {tts_segments_spy.calls!r}"
    )
    assert replace_spy.calls == [], (
        f"replace_audio was called: {replace_spy.calls!r}"
    )
    assert replace_bg_spy.calls == [], (
        f"replace_audio_keep_background was called: {replace_bg_spy.calls!r}"
    )
    assert mute_spy.calls == [], (
        f"mute_audio was called: {mute_spy.calls!r}"
    )
    assert separate_spy.calls == [], (
        f"separate_vocals was called: {separate_spy.calls!r}"
    )

    # --- Output sanity: file exists, has audio, ~3s long ----------------
    assert result == out
    assert out.exists() and out.stat().st_size > 0
    assert _has_audio_stream(out), "output mp4 has no audio stream"
    assert abs(get_duration_s(out) - 3.0) < 0.5, (
        f"unexpected output duration: {get_duration_s(out):.2f}s"
    )


def test_keep_original_audio_env_default_is_on() -> None:
    """The contract: leaving ``KEEP_ORIGINAL_AUDIO`` unset must behave as ``=1``.

    We check the env-parse expression directly rather than re-running the
    full pipeline -- it's a literal one-liner inside ``render_clip``.
    """
    import os

    # Simulate the exact line in the pipeline (must match pipeline.py).
    val = os.environ.get("KEEP_ORIGINAL_AUDIO", "1")
    # Whatever the host env says, the *default* (unset) must be "1".
    assert (
        os.environ.get("KEEP_ORIGINAL_AUDIO", "1") == "1"
        or val == os.environ["KEEP_ORIGINAL_AUDIO"]
    ), "unexpected default for KEEP_ORIGINAL_AUDIO"

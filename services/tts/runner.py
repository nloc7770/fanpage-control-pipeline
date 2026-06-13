"""Vietnamese text-to-speech runner backed by the TTS Hub REST API.

This module used to load F5-TTS in-process. It now delegates synthesis to the
external Vietnamese TTS Hub (see ``TTS-master/api/app.py``) over HTTP, which
exposes higher-quality voice presets (including ``f5_deep_male``) via the
``tune_layers`` parameter on ``POST /api/tts``.

Public surface kept identical so the rest of the pipeline does not change:

* :class:`TTSError`
* :func:`synthesize_vi(text, output_path, voice=None) -> Path`
* :func:`synthesize_vi_segments(segments, clip_duration_s, output_path, ...) -> Path`

Configuration via environment variables:

* ``TTS_HUB_URL``        -- default ``http://127.0.0.1:9090``
* ``TTS_HUB_TIMEOUT_S``  -- per-request HTTP timeout, default ``120``
* ``TTS_HUB_MODEL``      -- hub ``model=`` form field, default ``f5``
* ``TTS_VOICE_VI``       -- internal voice name (default ``male``), mapped to
  a hub ``tune_layers`` chain via :data:`_VOICE_PRESETS_HUB`.

The hub is pinged once per process; if it is unreachable, the first call to
:func:`synthesize_vi` raises a :class:`TTSError` with a clear message.
"""

from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any

import httpx
from loguru import logger


# ---------------------------------------------------------------------------
# Public errors
# ---------------------------------------------------------------------------


class TTSError(RuntimeError):
    """Raised when the TTS hub cannot be reached or returns an error."""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def _hub_url() -> str:
    return os.environ.get("TTS_HUB_URL", "http://127.0.0.1:9090").rstrip("/")


def _hub_timeout_s() -> float:
    try:
        return float(os.environ.get("TTS_HUB_TIMEOUT_S", "120"))
    except (TypeError, ValueError):
        return 120.0


def _hub_model() -> str:
    return os.environ.get("TTS_HUB_MODEL", "f5")


# Voice presets map our internal names to TTS hub ``tune_layers`` chains.
# The hub composes layer params on top of F5 defaults, so each preset is a
# comma-separated list of layer names. The default voice is ``male`` per the
# user request -- the hub's ``f5_deep_male`` preset gives a clean male voice.
_VOICE_PRESETS_HUB: dict[str, str] = {
    "default": "f5_balanced,f5_clarity",
    "narrator": "f5_warm_story,f5_balanced",
    "male": "f5_deep_male",
    "female": "f5_bright_female",
}


def _resolve_layers(voice: str | None) -> str:
    voice_name = (voice or os.environ.get("TTS_VOICE_VI") or "male").lower()
    return _VOICE_PRESETS_HUB.get(voice_name, _VOICE_PRESETS_HUB["male"])


# Long-text behaviour: the hub handles long inputs reasonably, but we still
# chunk very long narration so any single failure is bounded in scope.
_CHUNK_CHAR_THRESHOLD = 500
_CHUNK_MAX_CHARS = 250
_INTER_CHUNK_SILENCE_S = 0.150
_TARGET_SR = 24000


# ---------------------------------------------------------------------------
# Hub liveness probe (lazy, cached for the lifetime of the process)
# ---------------------------------------------------------------------------


_HUB_ALIVE: bool | None = None
_HUB_LOCK = threading.Lock()


def _check_hub_alive(force: bool = False) -> None:
    """Ping the hub once per process; raise TTSError on failure.

    Called from the top of each public synthesis function so we fail fast
    with a clear message rather than after every chunk attempt times out.
    """
    global _HUB_ALIVE
    if _HUB_ALIVE and not force:
        return
    with _HUB_LOCK:
        if _HUB_ALIVE and not force:
            return
        url = _hub_url()
        try:
            with httpx.Client(timeout=min(10.0, _hub_timeout_s())) as client:
                # ``/api/tune-layers`` is the cheapest authenticated probe.
                r = client.get(f"{url}/api/tune-layers")
                r.raise_for_status()
        except Exception as exc:  # noqa: BLE001 -- surface as typed error
            raise TTSError(
                f"TTS hub unreachable at {url}: {exc}. "
                "Set TTS_HUB_URL or start the hub "
                "('python3 -m uvicorn api.app:app --port 9090' in TTS-master)."
            ) from exc
        _HUB_ALIVE = True
        logger.info("tts: hub reachable at {}", url)


# ---------------------------------------------------------------------------
# HTTP synthesis (one chunk == one hub call)
# ---------------------------------------------------------------------------


def _hub_synthesize(text: str, out_path: Path, voice: str | None) -> None:
    """Synthesize ``text`` via the TTS hub, downloading the wav to ``out_path``.

    The wav is then re-encoded in-place to 24 kHz mono pcm_s16le so callers
    can rely on a uniform format (the hub may emit other sample rates).
    """
    layers = _resolve_layers(voice)
    url = _hub_url()
    model = _hub_model()
    timeout = _hub_timeout_s()

    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(
                f"{url}/api/tts",
                data={
                    "text": text,
                    "model": model,
                    "tune_layers": layers,
                    "normalize": "true",
                },
            )
            r.raise_for_status()
            body = r.json()
            audio_url = body.get("audio_url")
            if not audio_url:
                raise TTSError(
                    f"hub /api/tts returned no audio_url; body={body!r}"
                )

            r2 = client.get(f"{url}{audio_url}")
            r2.raise_for_status()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(r2.content)
    except httpx.HTTPStatusError as exc:
        detail = ""
        try:
            detail = exc.response.text[:500]
        except Exception:
            pass
        raise TTSError(
            f"hub HTTP {exc.response.status_code} for /api/tts: {detail}"
        ) from exc
    except httpx.HTTPError as exc:
        raise TTSError(f"hub HTTP error: {exc}") from exc

    if not out_path.exists() or out_path.stat().st_size == 0:
        raise TTSError(f"hub returned empty audio at {out_path}")

    # Re-encode to 24 kHz mono pcm_s16le for downstream uniformity.
    try:
        _normalise_wav_inplace(out_path)
    except TTSError:
        # If normalisation fails the raw download is still on disk -- but
        # the format may not be what the rest of the pipeline expects. Bubble
        # up so the caller can skip this chunk and continue.
        raise


# ---------------------------------------------------------------------------
# Text splitting
# ---------------------------------------------------------------------------


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[\.!?])\s+|\n+")


def _split_sentences(text: str) -> list[str]:
    """Split on `. `, `! `, `? `, or newline; preserve trailing punctuation."""
    pieces = [p.strip() for p in _SENTENCE_SPLIT_RE.split(text) if p and p.strip()]
    if not pieces:
        return []

    chunks: list[str] = []
    buf = ""
    for piece in pieces:
        if not buf:
            buf = piece
        elif len(buf) + 1 + len(piece) <= _CHUNK_MAX_CHARS:
            buf = f"{buf} {piece}"
        else:
            chunks.append(buf)
            buf = piece
    if buf:
        chunks.append(buf)

    final: list[str] = []
    for chunk in chunks:
        if len(chunk) <= _CHUNK_MAX_CHARS:
            final.append(chunk)
            continue
        words = chunk.split()
        cur = ""
        for w in words:
            if not cur:
                cur = w
            elif len(cur) + 1 + len(w) <= _CHUNK_MAX_CHARS:
                cur = f"{cur} {w}"
            else:
                final.append(cur)
                cur = w
        if cur:
            final.append(cur)
    return final


# ---------------------------------------------------------------------------
# ffmpeg + wav helpers (unchanged from the in-process implementation)
# ---------------------------------------------------------------------------


def _write_silence_wav(path: Path, duration_s: float, sample_rate: int = _TARGET_SR) -> None:
    """Write a mono 16-bit PCM silence wav to ``path``."""
    n_samples = int(duration_s * sample_rate)
    n_channels = 1
    bits_per_sample = 16
    byte_rate = sample_rate * n_channels * bits_per_sample // 8
    block_align = n_channels * bits_per_sample // 8
    data_size = n_samples * block_align

    with path.open("wb") as fh:
        fh.write(b"RIFF")
        fh.write(struct.pack("<I", 36 + data_size))
        fh.write(b"WAVE")
        fh.write(b"fmt ")
        fh.write(struct.pack("<I", 16))
        fh.write(struct.pack("<H", 1))  # PCM
        fh.write(struct.pack("<H", n_channels))
        fh.write(struct.pack("<I", sample_rate))
        fh.write(struct.pack("<I", byte_rate))
        fh.write(struct.pack("<H", block_align))
        fh.write(struct.pack("<H", bits_per_sample))
        fh.write(b"data")
        fh.write(struct.pack("<I", data_size))
        fh.write(b"\x00" * data_size)


def _concat_wavs(parts: list[Path], output_path: Path) -> None:
    """Concatenate ``parts`` to ``output_path`` via ffmpeg concat demuxer.

    Re-encodes to 24kHz mono PCM s16le.
    """
    if not parts:
        raise TTSError("no chunks to concatenate; all synthesis attempts failed.")

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    ) as listfile:
        list_path = Path(listfile.name)
        for p in parts:
            listfile.write(f"file '{p.resolve().as_posix()}'\n")

    try:
        cmd = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            "-ar",
            str(_TARGET_SR),
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(output_path),
        ]
        logger.debug("tts: concat cmd={}", " ".join(cmd))
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
        if proc.returncode != 0:
            raise TTSError(
                f"ffmpeg concat failed (rc={proc.returncode}): {proc.stderr.strip()}"
            )
    finally:
        try:
            list_path.unlink(missing_ok=True)
        except OSError:
            pass


def _normalise_wav_inplace(path: Path) -> None:
    """Force ``path`` to 24kHz mono pcm_s16le via ffmpeg, in-place."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        cmd = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-ar",
            str(_TARGET_SR),
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(tmp_path),
        ]
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
        if proc.returncode != 0:
            raise TTSError(
                f"ffmpeg normalise failed (rc={proc.returncode}): {proc.stderr.strip()}"
            )
        shutil.move(str(tmp_path), str(path))
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _probe_wav_duration_s(path: Path) -> float:
    """Best-effort duration probe via wave.open then ffprobe fallback."""
    try:
        import wave

        with wave.open(str(path), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate() or 1
            return float(frames) / float(rate)
    except Exception:
        pass
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        return float((proc.stdout or "0").strip() or 0.0)
    except Exception:
        return 0.0


def _apply_atempo(src: Path, dst: Path, tempo: float, sample_rate: int) -> None:
    """Re-encode ``src`` -> ``dst`` with an atempo filter applied (capped 1.5)."""
    tempo = max(0.5, min(1.5, float(tempo)))
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(src),
        "-filter:a",
        f"atempo={tempo:.4f}",
        "-ar",
        str(sample_rate),
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        str(dst),
    ]
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        raise TTSError(
            f"ffmpeg atempo failed (rc={proc.returncode}): {proc.stderr.strip()}"
        )


def _fit_wav_to_duration(
    src: Path, dst: Path, duration_s: float, sample_rate: int
) -> None:
    """Pad-with-silence and/or trim ``src`` to exactly ``duration_s`` seconds.

    Output is 24kHz mono pcm_s16le.
    """
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(src),
        "-af",
        f"apad,atrim=duration={duration_s:.4f},asetpts=PTS-STARTPTS",
        "-ar",
        str(sample_rate),
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        str(dst),
    ]
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        raise TTSError(
            f"ffmpeg fit_to_duration failed (rc={proc.returncode}): {proc.stderr.strip()}"
        )


def _force_wav_duration(path: Path, duration_s: float, sample_rate: int) -> None:
    """Trim or pad ``path`` in-place to exactly ``duration_s`` seconds."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        _fit_wav_to_duration(path, tmp_path, duration_s, sample_rate)
        shutil.move(str(tmp_path), str(path))
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Public synthesis API
# ---------------------------------------------------------------------------


def synthesize_vi(
    text: str,
    output_path: Path,
    voice: str | None = None,
) -> Path:
    """Synthesise Vietnamese ``text`` to a 24kHz mono PCM WAV at ``output_path``.

    Parameters
    ----------
    text
        Vietnamese narration. 1-3 minutes of input is the design target.
    output_path
        Destination WAV path. Parent dirs are created on demand.
    voice
        Voice preset name; defaults to ``$TTS_VOICE_VI`` then ``"male"``.

    Returns
    -------
    Path
        ``output_path`` once the file is on disk.

    Raises
    ------
    TTSError
        On hub connectivity failures, HTTP errors, or if every chunk fails.
    """
    if not text or not text.strip():
        raise TTSError("synthesize_vi: text is empty.")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    _check_hub_alive()

    text = text.strip()
    if len(text) <= _CHUNK_CHAR_THRESHOLD:
        chunks = [text]
    else:
        chunks = _split_sentences(text) or [text]

    logger.info(
        "tts: hub-synthesising {} char(s) in {} chunk(s) (voice={})",
        len(text),
        len(chunks),
        voice or os.environ.get("TTS_VOICE_VI") or "male",
    )

    with tempfile.TemporaryDirectory(prefix="tts_") as tmpdir_str:
        tmpdir = Path(tmpdir_str)
        rendered: list[Path] = []
        silence_path = tmpdir / "_silence.wav"
        _write_silence_wav(silence_path, _INTER_CHUNK_SILENCE_S, _TARGET_SR)

        for idx, chunk in enumerate(chunks):
            chunk_path = tmpdir / f"chunk_{idx:04d}.wav"
            try:
                _hub_synthesize(chunk, chunk_path, voice)
            except Exception as exc:  # noqa: BLE001 - log+skip per spec
                logger.warning(
                    "tts: chunk {} ({} chars) failed: {} -- skipping",
                    idx,
                    len(chunk),
                    exc,
                )
                continue
            if not chunk_path.exists() or chunk_path.stat().st_size == 0:
                logger.warning("tts: chunk {} produced empty wav; skipping", idx)
                continue
            rendered.append(chunk_path)
            if idx < len(chunks) - 1:
                rendered.append(silence_path)

        if not rendered:
            raise TTSError("all TTS chunks failed; nothing to write.")

        if len(rendered) == 1:
            shutil.copy2(rendered[0], output_path)
            _normalise_wav_inplace(output_path)
        else:
            _concat_wavs(rendered, output_path)

    logger.info("tts: wrote {} ({} bytes)", output_path, output_path.stat().st_size)
    return output_path


def synthesize_vi_segments(
    segments: list[dict[str, Any]],
    clip_duration_s: float,
    output_path: Path,
    *,
    voice: str | None = None,
    sample_rate: int = _TARGET_SR,
) -> Path:
    """Synthesise time-aligned Vietnamese narration segments to one WAV.

    ``segments`` is a list of ``{"start": float, "end": float, "text_vi": str}``
    with times in seconds **relative to the clip** (0 = clip start). Each
    segment is synthesised independently via the TTS hub; per-segment wavs are
    placed onto a single track of ``clip_duration_s`` length with silence in
    the gaps (and trimmed/atempo'd to fit when the synthesis overruns).

    Behaviour per segment:

    * If the synthesised wav is longer than its slot, apply ``atempo`` to
      speed it up (capped at 1.5 -- F5-TTS quality drops above that). Any
      remainder is trimmed by the final apad/atrim guard.
    * If shorter, trailing silence pads out the slot naturally.

    Returns ``output_path`` (PCM 24 kHz mono WAV, duration == ``clip_duration_s``).
    """
    if clip_duration_s <= 0:
        raise TTSError("synthesize_vi_segments: clip_duration_s must be > 0.")

    cleaned: list[dict[str, Any]] = []
    for s in segments or []:
        try:
            s_start = max(0.0, float(s.get("start", 0.0)))
            s_end = float(s.get("end", s_start))
        except (TypeError, ValueError):
            continue
        if s_end > clip_duration_s:
            s_end = clip_duration_s
        if s_end <= s_start:
            continue
        txt = (s.get("text_vi") or s.get("text") or "").strip()
        if not txt:
            continue
        cleaned.append({"start": s_start, "end": s_end, "text": txt})
    cleaned.sort(key=lambda s: s["start"])

    if not cleaned:
        raise TTSError("synthesize_vi_segments: no valid segments to render.")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    _check_hub_alive()

    with tempfile.TemporaryDirectory(prefix="tts_seg_") as tmpdir_str:
        tmpdir = Path(tmpdir_str)
        timeline_parts: list[Path] = []
        cursor = 0.0

        for idx, seg in enumerate(cleaned):
            slot_start = seg["start"]
            slot_end = seg["end"]
            slot_dur = max(0.05, slot_end - slot_start)

            # Pre-segment silence (gap between cursor and slot_start).
            if slot_start > cursor + 0.005:
                gap_dur = slot_start - cursor
                gap_path = tmpdir / f"gap_{idx:04d}.wav"
                _write_silence_wav(gap_path, gap_dur, sample_rate)
                timeline_parts.append(gap_path)
                cursor = slot_start

            # Render the segment via the hub.
            raw_path = tmpdir / f"seg_{idx:04d}_raw.wav"
            try:
                _hub_synthesize(seg["text"], raw_path, voice)
            except Exception as exc:  # noqa: BLE001 -- log + use silence
                logger.warning(
                    "tts: segment {} ({} chars) failed: {} -- substituting silence",
                    idx, len(seg["text"]), exc,
                )
                sil_path = tmpdir / f"seg_{idx:04d}_silence.wav"
                _write_silence_wav(sil_path, slot_dur, sample_rate)
                timeline_parts.append(sil_path)
                cursor = slot_end
                continue

            if not raw_path.exists() or raw_path.stat().st_size == 0:
                logger.warning("tts: segment {} produced empty wav; using silence", idx)
                sil_path = tmpdir / f"seg_{idx:04d}_silence.wav"
                _write_silence_wav(sil_path, slot_dur, sample_rate)
                timeline_parts.append(sil_path)
                cursor = slot_end
                continue

            tts_dur = _probe_wav_duration_s(raw_path)

            slot_path = tmpdir / f"seg_{idx:04d}_slot.wav"
            if tts_dur > slot_dur * 1.15 and slot_dur > 0:
                needed = tts_dur / slot_dur
                tempo = min(1.5, max(1.0, needed))
                try:
                    _apply_atempo(raw_path, slot_path, tempo, sample_rate)
                except TTSError as exc:
                    logger.warning(
                        "tts: atempo failed for segment {} ({}); using raw wav",
                        idx, exc,
                    )
                    shutil.copy2(raw_path, slot_path)
            else:
                shutil.copy2(raw_path, slot_path)
                try:
                    _normalise_wav_inplace(slot_path)
                except TTSError as exc:
                    logger.warning(
                        "tts: normalise failed for segment {} ({}); keeping raw",
                        idx, exc,
                    )

            fitted_path = tmpdir / f"seg_{idx:04d}_fitted.wav"
            _fit_wav_to_duration(slot_path, fitted_path, slot_dur, sample_rate)
            timeline_parts.append(fitted_path)
            cursor = slot_end

        if cursor < clip_duration_s - 0.005:
            tail_dur = clip_duration_s - cursor
            tail_path = tmpdir / "tail_silence.wav"
            _write_silence_wav(tail_path, tail_dur, sample_rate)
            timeline_parts.append(tail_path)

        if not timeline_parts:
            raise TTSError("synthesize_vi_segments: nothing to concatenate.")

        _concat_wavs(timeline_parts, output_path)
        _force_wav_duration(output_path, clip_duration_s, sample_rate)

    logger.info(
        "tts: wrote {} ({} bytes, {} segments)",
        output_path,
        output_path.stat().st_size,
        len(cleaned),
    )
    return output_path

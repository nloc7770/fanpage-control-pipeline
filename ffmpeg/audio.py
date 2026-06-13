"""Silence detection, speech-interval helpers, and audio replace/mute.

The render pipeline uses these to "tighten" a raw cut: detect long silences
inside the clip with ffmpeg's ``silencedetect`` filter, then keep only the
speech windows. The result is a list of ``(start, end)`` time pairs in the
*source* timeline that we feed to :func:`ffmpeg.cut.cut_concat`.

This module also exposes :func:`replace_audio` (swap the soundtrack with a
synthesised voiceover) and :func:`mute_audio` (replace audio with silence) --
both used by the Vietnamese TTS voiceover stage in the render pipeline.

In addition, this module exposes a vocal-separation-aware variant
(:func:`replace_audio_keep_background`) that splits the source audio into
``vocals`` + ``instrumental`` (using Demucs, with an ffmpeg center-channel
cancel fallback) and mixes the Vietnamese TTS on top of the instrumental --
so background music, ambient, splashes, animal sounds, etc. are preserved
while the original speaker is removed.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from loguru import logger


def _ffmpeg_bin() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def _video_duration_s(path: str | Path) -> float:
    """Best-effort duration probe; returns 0.0 on failure."""
    try:
        from ffmpeg.probe import get_duration_s

        return float(get_duration_s(path))
    except Exception:
        return 0.0


# ``silencedetect`` emits lines like:
#   [silencedetect @ 0x...] silence_start: 12.345
#   [silencedetect @ 0x...] silence_end: 12.987 | silence_duration: 0.642
_RE_START = re.compile(r"silence_start:\s*(-?\d+(?:\.\d+)?)")
_RE_END = re.compile(r"silence_end:\s*(-?\d+(?:\.\d+)?)")


def detect_silence(
    input_path: str | Path,
    *,
    min_silence_ms: int = 350,
    threshold_db: float = -30.0,
) -> list[tuple[float, float]]:
    """Detect silences in ``input_path`` and return ``[(start_s, end_s), ...]``.

    Uses ffmpeg's ``silencedetect`` audio filter and parses its stderr. Returns
    silences with duration >= ``min_silence_ms``. If ffmpeg fails or no audio
    stream is present the function returns an empty list rather than raising
    -- callers should treat "no silences" as "skip tightening".
    """
    min_d = max(0.05, float(min_silence_ms) / 1000.0)
    cmd = [
        _ffmpeg_bin(),
        "-hide_banner",
        "-nostats",
        "-i",
        str(input_path),
        "-af",
        f"silencedetect=noise={threshold_db:.1f}dB:d={min_d:.3f}",
        "-f",
        "null",
        "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return []
    # silencedetect writes to stderr, but we don't trust which stream gets it
    # across ffmpeg versions, so scan both.
    blob = (proc.stderr or "") + "\n" + (proc.stdout or "")

    starts = [float(m.group(1)) for m in _RE_START.finditer(blob)]
    ends = [float(m.group(1)) for m in _RE_END.finditer(blob)]

    silences: list[tuple[float, float]] = []
    # Pair starts with ends in order. A silence that runs past EOF has no
    # matching ``silence_end``; we drop it (cut_concat will keep the tail).
    for i, start in enumerate(starts):
        if i < len(ends):
            end = ends[i]
            if end > start and (end - start) >= min_d:
                silences.append((max(0.0, start), end))
    return silences


def compute_speech_intervals(
    duration_s: float, silences: list[tuple[float, float]]
) -> list[tuple[float, float]]:
    """Invert ``silences`` over ``[0, duration_s]`` to get speech windows.

    Returns a list of ``(start, end)`` covering the parts of the clip that are
    *not* silent. Adjacent intervals are merged. Trims to ``[0, duration_s]``.
    """
    if duration_s <= 0:
        return []
    if not silences:
        return [(0.0, float(duration_s))]

    # Sort + clip silences to the clip duration.
    cleaned: list[tuple[float, float]] = []
    for s, e in sorted(silences):
        s = max(0.0, float(s))
        e = min(float(duration_s), float(e))
        if e > s:
            cleaned.append((s, e))

    intervals: list[tuple[float, float]] = []
    cursor = 0.0
    for s, e in cleaned:
        if s > cursor:
            intervals.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < duration_s:
        intervals.append((cursor, float(duration_s)))

    # Drop sub-50ms slivers; ffmpeg's trim does not love those.
    return [(a, b) for (a, b) in intervals if (b - a) >= 0.05]


def remap_time(t_local: float, intervals: list[tuple[float, float]]) -> float | None:
    """Re-map a timestamp in the *original* cut timeline onto the tightened
    timeline produced by concatenating ``intervals``.

    Returns ``None`` if ``t_local`` falls inside a removed (silent) region.
    """
    if not intervals:
        return float(t_local)
    elapsed = 0.0
    for a, b in intervals:
        if t_local < a:
            return None  # in a removed silence before this interval
        if t_local <= b:
            return elapsed + (t_local - a)
        elapsed += b - a
    # past the end: clamp to total
    return elapsed


def total_kept(intervals: list[tuple[float, float]]) -> float:
    """Total seconds across all speech intervals."""
    return sum(max(0.0, b - a) for a, b in intervals)


# ---------------------------------------------------------------------------
# Audio replacement / muting (used by the Vietnamese TTS voiceover stage).
# ---------------------------------------------------------------------------


def replace_audio(
    video_path: str | Path,
    audio_path: str | Path,
    output_path: str | Path,
    *,
    fit_to: str = "video",
) -> Path:
    """Mute the source audio of ``video_path`` and replace it with ``audio_path``.

    The video stream is stream-copied (``-c:v copy``) so this is fast and
    lossless. The audio is re-encoded to AAC stereo 44.1 kHz (mobile-friendly).

    Parameters
    ----------
    video_path:
        The input video. Its existing audio track is discarded entirely.
    audio_path:
        The replacement audio (typically a TTS ``.wav``).
    output_path:
        Final ``.mp4`` path.
    fit_to:
        How to reconcile audio/video duration mismatches:

        * ``"video"`` (default): keep the video timeline intact. If the TTS is
          shorter than the video, pad it with silence (so subtitles + zoom
          keyframes stay in sync). If the TTS is **longer** than the video,
          subtly speed it up via ``atempo`` so it fits, then trim any
          remainder. We never *lengthen* the video here -- callers that need
          ``narrate-then-pad-video`` semantics should pass ``fit_to="audio"``.
        * ``"audio"``: stretch the video timeline to fit the audio. Used when
          the narration is the authoritative duration and the visuals should
          slow to match. Implemented via ``setpts`` on the video.

    Returns
    -------
    Path
        ``output_path``, for chaining.
    """
    video_path = Path(video_path)
    audio_path = Path(audio_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not video_path.exists():
        raise FileNotFoundError(f"video not found: {video_path}")
    if not audio_path.exists():
        raise FileNotFoundError(f"audio not found: {audio_path}")

    v_dur = _video_duration_s(video_path)
    a_dur = _video_duration_s(audio_path)

    if fit_to not in ("video", "audio"):
        raise ValueError(f"fit_to must be 'video' or 'audio', got {fit_to!r}")

    bin_ = _ffmpeg_bin()

    if fit_to == "audio":
        # Stretch video to match audio duration via setpts. Audio is re-encoded
        # as-is (no atempo). Falls back to '-shortest' if duration probing
        # failed.
        if v_dur <= 0 or a_dur <= 0:
            ratio = 1.0
        else:
            ratio = a_dur / v_dur
        # setpts factor: 1/ratio (factor < 1 = speed up, > 1 = slow down).
        # Clamp to a sane range so a busted probe can't produce a 100x slowdown.
        setpts_factor = max(0.25, min(4.0, ratio))
        vf = f"setpts={setpts_factor:.6f}*PTS"
        cmd = [
            bin_, "-y", "-loglevel", "error",
            "-i", str(video_path),
            "-i", str(audio_path),
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
            str(output_path),
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        return output_path

    # fit_to == "video"
    # Build an -af expression that:
    #   * speeds up the audio with atempo if it's longer than the video (subtle,
    #     factor clamped between 1.0 and 1.10 -- beyond that it sounds bad);
    #   * pads with silence at the tail if it's shorter;
    #   * always atrim to exactly v_dur so we never overshoot.
    af_parts: list[str] = []
    if v_dur > 0 and a_dur > v_dur:
        # speed-up factor required to fit
        needed = a_dur / v_dur
        # Cap at 1.10 -- we'd rather trim a few syllables off the end than
        # produce chipmunk audio. atempo accepts [0.5, 100].
        tempo = min(1.10, needed)
        if tempo > 1.0001:
            af_parts.append(f"atempo={tempo:.4f}")
    # Always pad+trim to exact video duration when we know it. apad emits
    # infinite silence after the source ends; atrim then bounds it.
    if v_dur > 0:
        af_parts.append("apad")
        af_parts.append(f"atrim=duration={v_dur:.3f}")
        af_parts.append("asetpts=PTS-STARTPTS")
    af = ",".join(af_parts) if af_parts else "anull"

    cmd = [
        bin_, "-y", "-loglevel", "error",
        "-i", str(video_path),
        "-i", str(audio_path),
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
        "-af", af,
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
        # -shortest is a belt+suspenders guard in case our apad+atrim didn't
        # bound things tightly enough.
        "-shortest",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return output_path


# ---------------------------------------------------------------------------
# Vocal separation + background-preserving voiceover splice.
# ---------------------------------------------------------------------------


def extract_audio(
    video_path: str | Path,
    output_wav: str | Path,
    *,
    sample_rate: int = 44100,
) -> Path:
    """Extract the audio track from ``video_path`` as PCM stereo wav.

    Stereo is required because Demucs's pretrained ``htdemucs`` model expects
    stereo inputs. Mono inputs are upmixed via ``-ac 2``.
    """
    video_path = Path(video_path)
    output_wav = Path(output_wav)
    output_wav.parent.mkdir(parents=True, exist_ok=True)

    if not video_path.exists():
        raise FileNotFoundError(f"video not found: {video_path}")

    cmd = [
        _ffmpeg_bin(), "-y", "-loglevel", "error",
        "-i", str(video_path),
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", str(int(sample_rate)),
        "-ac", "2",
        str(output_wav),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return output_wav


def _ffmpeg_center_cancel_instrumental(
    audio_path: Path, output_dir: Path
) -> tuple[Path, Path]:
    """Fallback: ffmpeg center-channel cancel to approximate vocal removal.

    Vocals on most TV / YouTube content are mixed center-mono. Subtracting
    the two stereo channels (``c0-c1`` and ``c1-c0``) nukes the center and
    keeps the stereo-spread instrumental. Very lossy, but instant and never
    fails. We also emit a "vocals" companion as ``(c0+c1)/2`` so callers
    that ask for both stems still get something coherent.
    """
    instr = output_dir / "no_vocals.wav"
    voc = output_dir / "vocals.wav"
    # Instrumental: subtract center.
    subprocess.run(
        [
            _ffmpeg_bin(), "-y", "-loglevel", "error",
            "-i", str(audio_path),
            "-af",
            "pan=stereo|c0=c0-c1|c1=c1-c0,highpass=f=80,lowpass=f=18000",
            "-ar", "44100", "-ac", "2",
            str(instr),
        ],
        check=True,
        capture_output=True,
    )
    # Vocals (approx): center-mono = (c0+c1)/2 duplicated to stereo.
    subprocess.run(
        [
            _ffmpeg_bin(), "-y", "-loglevel", "error",
            "-i", str(audio_path),
            "-af",
            "pan=stereo|c0=0.5*c0+0.5*c1|c1=0.5*c0+0.5*c1",
            "-ar", "44100", "-ac", "2",
            str(voc),
        ],
        check=True,
        capture_output=True,
    )
    return voc, instr


def separate_vocals(
    audio_path: str | Path,
    output_dir: str | Path,
    *,
    device: str = "cuda",
    use_demucs: bool = True,
) -> tuple[Path, Path]:
    """Split ``audio_path`` into ``(vocals_wav, instrumental_wav)``.

    Primary path: invoke Demucs ``htdemucs`` via subprocess with
    ``--two-stems vocals`` so it emits exactly ``vocals.wav`` and
    ``no_vocals.wav``. We move/rename them to ``<output_dir>/vocals.wav`` and
    ``<output_dir>/instrumental.wav``.

    Fallback path: if demucs isn't installed, fails to launch, or
    ``use_demucs=False`` is set, use an ffmpeg center-channel cancel filter
    to approximate an instrumental track. Lossy, but never fails.
    """
    audio_path = Path(audio_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not audio_path.exists():
        raise FileNotFoundError(f"audio not found: {audio_path}")

    target_voc = output_dir / "vocals.wav"
    target_instr = output_dir / "instrumental.wav"

    if use_demucs:
        # Demucs writes to <out>/htdemucs/<stem>/{vocals,no_vocals}.wav.
        demucs_out = output_dir / "_demucs"
        demucs_out.mkdir(parents=True, exist_ok=True)
        cmd = [
            sys.executable, "-m", "demucs",
            "--two-stems", "vocals",
            "-o", str(demucs_out),
            "-n", "htdemucs",
            "--device", device,
            str(audio_path),
        ]
        try:
            proc = subprocess.run(
                cmd, check=False, capture_output=True, text=True,
                timeout=600,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            logger.warning(
                "audio: demucs invocation failed ({}); using ffmpeg center-cancel fallback",
                exc,
            )
            return _ffmpeg_center_cancel_instrumental(audio_path, output_dir)

        if proc.returncode != 0:
            logger.warning(
                "audio: demucs exited {} on {} (stderr={}); falling back to ffmpeg center-cancel",
                proc.returncode,
                audio_path.name,
                (proc.stderr or "")[-400:],
            )
            return _ffmpeg_center_cancel_instrumental(audio_path, output_dir)

        # Locate the produced files. Stem name = audio_path.stem.
        stem = audio_path.stem
        produced_voc = demucs_out / "htdemucs" / stem / "vocals.wav"
        produced_instr = demucs_out / "htdemucs" / stem / "no_vocals.wav"
        if not produced_voc.exists() or not produced_instr.exists():
            # Demucs sometimes substitutes punctuation in the stem; just glob.
            candidates = list((demucs_out / "htdemucs").glob("*/vocals.wav"))
            if candidates:
                produced_voc = candidates[0]
                produced_instr = candidates[0].with_name("no_vocals.wav")
        if not produced_voc.exists() or not produced_instr.exists():
            logger.warning(
                "audio: demucs ran but produced no expected files (looked under {}); "
                "falling back to ffmpeg center-cancel",
                demucs_out,
            )
            return _ffmpeg_center_cancel_instrumental(audio_path, output_dir)

        # Move to stable locations.
        shutil.move(str(produced_voc), str(target_voc))
        shutil.move(str(produced_instr), str(target_instr))
        # Clean up demucs scratch tree.
        shutil.rmtree(demucs_out, ignore_errors=True)
        return target_voc, target_instr

    # use_demucs=False path.
    return _ffmpeg_center_cancel_instrumental(audio_path, output_dir)


def mix_voiceover_with_background(
    instrumental: str | Path,
    voiceover: str | Path,
    output: str | Path,
    *,
    bg_gain_db: float = -8.0,
    vo_gain_db: float = 0.0,
    duck_bg: bool = True,
    duck_threshold: float = 0.05,
    duck_ratio: float = 6.0,
) -> Path:
    """Mix ``instrumental`` (background) under ``voiceover`` (TTS).

    Output is stereo 44.1 kHz PCM wav. The voiceover sits at ``vo_gain_db``
    (default 0 dB, full) while the instrumental is dropped to ``bg_gain_db``
    (default -8 dB). When ``duck_bg=True`` (default), a side-chain compressor
    listens to the voiceover and ducks the background further whenever speech
    is present, so the TTS stays intelligible.

    Mix duration is the **longest** of the two inputs so we don't truncate
    the instrumental tail if the TTS is short, and vice-versa.
    """
    instrumental = Path(instrumental)
    voiceover = Path(voiceover)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    if not instrumental.exists():
        raise FileNotFoundError(f"instrumental not found: {instrumental}")
    if not voiceover.exists():
        raise FileNotFoundError(f"voiceover not found: {voiceover}")

    # Build a filter_complex graph.
    #
    # Inputs:
    #   [0:a] = instrumental
    #   [1:a] = voiceover (TTS)
    #
    # 1. Normalise sample rates / channels for both to stereo 44.1 kHz.
    # 2. Apply per-input gains (volume filter, dB).
    # 3. If ducking, side-chain compress instrumental against voiceover.
    # 4. amix the two streams (duration=longest).
    bg_gain = float(bg_gain_db)
    vo_gain = float(vo_gain_db)

    if duck_bg:
        # Use sidechaincompress on the instrumental, keyed by the voiceover.
        # We need two copies of the voiceover: one for the sidechain key,
        # one for the final mix.
        filter_complex = (
            f"[0:a]aformat=channel_layouts=stereo:sample_rates=44100,"
            f"volume={bg_gain:.2f}dB[bg];"
            f"[1:a]aformat=channel_layouts=stereo:sample_rates=44100,"
            f"volume={vo_gain:.2f}dB,asplit=2[vo1][vo2];"
            f"[bg][vo1]sidechaincompress=threshold={duck_threshold:.3f}:"
            f"ratio={duck_ratio:.2f}:attack=10:release=250[bgduck];"
            f"[bgduck][vo2]amix=inputs=2:duration=longest:normalize=0[out]"
        )
    else:
        filter_complex = (
            f"[0:a]aformat=channel_layouts=stereo:sample_rates=44100,"
            f"volume={bg_gain:.2f}dB[bg];"
            f"[1:a]aformat=channel_layouts=stereo:sample_rates=44100,"
            f"volume={vo_gain:.2f}dB[vo];"
            f"[bg][vo]amix=inputs=2:duration=longest:normalize=0[out]"
        )

    cmd = [
        _ffmpeg_bin(), "-y", "-loglevel", "error",
        "-i", str(instrumental),
        "-i", str(voiceover),
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-ar", "44100",
        "-ac", "2",
        "-c:a", "pcm_s16le",
        str(output),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return output


def replace_audio_keep_background(
    video_path: str | Path,
    voiceover_path: str | Path,
    output_path: str | Path,
    *,
    fit_to: str = "video",
    bg_gain_db: float = -8.0,
    vo_gain_db: float = 0.0,
    duck_bg: bool = True,
    use_demucs: bool = True,
    device: str | None = None,
    scratch_dir: str | Path | None = None,
) -> Path:
    """High-level: extract -> separate -> mix -> mux.

    Drop-in replacement for :func:`replace_audio` that keeps the source's
    background music / SFX / ambient while removing the original speaker.

    Pipeline:

    1. Extract the source audio from ``video_path`` to a wav.
    2. Run vocal source separation (Demucs primary, ffmpeg center-cancel
       fallback) to split it into ``vocals.wav`` (discarded) and
       ``instrumental.wav`` (kept).
    3. Mix ``instrumental.wav`` under ``voiceover_path`` (the Vietnamese TTS)
       at ``bg_gain_db`` / ``vo_gain_db``, optionally side-chain ducking.
    4. Mux the mixed audio back onto the source video via :func:`replace_audio`
       (so the existing duration-matching / fit_to logic is reused for free).

    If anything fails (separation or mix), falls back to the plain
    :func:`replace_audio` behaviour (full replacement, no background).
    """
    video_path = Path(video_path)
    voiceover_path = Path(voiceover_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not video_path.exists():
        raise FileNotFoundError(f"video not found: {video_path}")
    if not voiceover_path.exists():
        raise FileNotFoundError(f"voiceover not found: {voiceover_path}")

    device = device or os.environ.get("DEMUCS_DEVICE", "cuda")

    # Use a stable scratch sub-dir so the demucs intermediate files don't get
    # left on disk if the caller's temp dir is reused.
    import tempfile as _tempfile

    own_scratch = scratch_dir is None
    if own_scratch:
        scratch = Path(_tempfile.mkdtemp(prefix="sff-bgkeep-"))
    else:
        scratch = Path(scratch_dir)
        scratch.mkdir(parents=True, exist_ok=True)

    try:
        source_wav = scratch / "source.wav"
        extract_audio(video_path, source_wav)

        try:
            _voc, instrumental = separate_vocals(
                source_wav, scratch, device=device, use_demucs=use_demucs
            )
        except Exception as exc:
            logger.warning(
                "audio: vocal separation crashed ({}); falling back to full "
                "audio replacement (background will be lost)",
                exc,
            )
            return replace_audio(
                video_path, voiceover_path, output_path, fit_to=fit_to
            )

        if not instrumental.exists() or instrumental.stat().st_size == 0:
            logger.warning(
                "audio: instrumental missing/empty after separation; falling "
                "back to full audio replacement"
            )
            return replace_audio(
                video_path, voiceover_path, output_path, fit_to=fit_to
            )

        # Build the mixed track (instrumental + voiceover).
        mixed = scratch / "mixed.wav"
        try:
            mix_voiceover_with_background(
                instrumental,
                voiceover_path,
                mixed,
                bg_gain_db=bg_gain_db,
                vo_gain_db=vo_gain_db,
                duck_bg=duck_bg,
            )
        except subprocess.CalledProcessError as exc:
            logger.warning(
                "audio: mix step failed ({}); falling back to full audio "
                "replacement",
                exc,
            )
            return replace_audio(
                video_path, voiceover_path, output_path, fit_to=fit_to
            )

        # Hand off to the existing replace_audio so duration-fit semantics
        # (apad / atempo / atrim / fit_to=audio stretch) are identical.
        return replace_audio(video_path, mixed, output_path, fit_to=fit_to)
    finally:
        if own_scratch:
            shutil.rmtree(scratch, ignore_errors=True)


def mute_audio(video_path: str | Path, output_path: str | Path) -> Path:
    """Replace the audio of ``video_path`` with silence matching its duration.

    Video is stream-copied; audio is freshly encoded as AAC stereo 44.1 kHz
    from ``anullsrc``. Used as a fallback when TTS synthesis fails: we still
    don't want to leak the original (English) dialog.
    """
    video_path = Path(video_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not video_path.exists():
        raise FileNotFoundError(f"video not found: {video_path}")

    cmd = [
        _ffmpeg_bin(), "-y", "-loglevel", "error",
        "-i", str(video_path),
        "-f", "lavfi",
        "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "96k",
        "-shortest",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return output_path

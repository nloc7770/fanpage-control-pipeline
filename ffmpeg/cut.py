"""Lossless / near-lossless segment cut + speech-interval concat."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path


def _ffmpeg_bin() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def cut_segment(
    input_path: str | Path,
    start: float,
    end: float,
    output_path: str | Path,
    *,
    reencode: bool = False,
) -> Path:
    """Cut ``[start, end]`` from ``input_path`` to ``output_path``.

    Tries stream-copy (``-c copy``) first for speed; pass ``reencode=True`` to
    force an H.264 re-encode if the source has out-of-keyframe cuts and you
    need frame-accurate trimming.
    """
    if end <= start:
        raise ValueError(f"end ({end}) must be > start ({start})")

    duration = end - start
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        _ffmpeg_bin(),
        "-y",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(input_path),
        "-t",
        f"{duration:.3f}",
        "-avoid_negative_ts",
        "make_zero",
    ]
    if reencode:
        cmd += [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
        ]
    else:
        cmd += ["-c", "copy"]
    cmd.append(str(output_path))

    subprocess.run(cmd, check=True, capture_output=True)
    return output_path


def cut_concat(
    input_path: str | Path,
    intervals: Sequence[tuple[float, float]],
    output_path: str | Path,
    *,
    reencode: bool = True,
    crossfade_s: float = 0.0,
) -> Path:
    """Cut ``input_path`` into the listed ``intervals`` and concatenate them.

    Each interval is ``(start_s, end_s)`` measured in the **input** timeline.
    The result is a single mp4 where the removed segments are gone and the
    surviving audio/video remain in sync. Implemented with a single
    ``-filter_complex`` graph using ``trim`` + ``atrim`` + ``concat`` so we
    never touch the disk between cuts.

    ``reencode`` is honored as a courtesy; the filter_complex path always
    re-encodes (concat demuxer can stream-copy but requires matching codec
    parameters across cuts, which we cannot guarantee from arbitrary
    timestamps). Pass ``reencode=False`` only as a hint to downstream callers.

    ``crossfade_s`` adds a dissolve transition between consecutive segments.
    Only applied when > 0 and there are multiple intervals. Each transition
    shortens the total output by ``crossfade_s`` per join.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Filter out degenerate intervals.
    clean = [(float(a), float(b)) for (a, b) in intervals if float(b) > float(a)]
    if not clean:
        raise ValueError("cut_concat requires at least one non-empty interval")

    # Fast path: one interval == plain cut.
    if len(clean) == 1:
        a, b = clean[0]
        return cut_segment(input_path, a, b, output_path, reencode=True)

    # Use crossfade path when requested and we have multiple segments.
    if crossfade_s > 0 and len(clean) > 1:
        return _cut_concat_crossfade(input_path, clean, output_path, crossfade_s)

    parts: list[str] = []
    concat_inputs: list[str] = []
    for i, (a, b) in enumerate(clean):
        parts.append(
            f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{i}]"
        )
        parts.append(
            f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[a{i}]"
        )
        concat_inputs.append(f"[v{i}][a{i}]")
    n = len(clean)
    parts.append(f"{''.join(concat_inputs)}concat=n={n}:v=1:a=1[outv][outa]")
    filter_complex = ";".join(parts)

    cmd = [
        _ffmpeg_bin(),
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
        "-filter_complex",
        filter_complex,
        "-map",
        "[outv]",
        "-map",
        "[outa]",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "18",
        "-c:a",
        "aac",
        "-b:a",
        "256k",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return output_path


def _cut_concat_crossfade(
    input_path: str | Path,
    intervals: list[tuple[float, float]],
    output_path: Path,
    crossfade_s: float,
) -> Path:
    """Concatenate intervals with xfade dissolve transitions between them.

    Uses ffmpeg xfade filter for video and acrossfade for audio. Each
    transition consumes ``crossfade_s`` from the end of the preceding segment
    and the start of the following segment.
    """
    n = len(intervals)

    # Build trim stages
    parts: list[str] = []
    for i, (a, b) in enumerate(intervals):
        parts.append(
            f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{i}]"
        )
        parts.append(
            f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[a{i}]"
        )

    # Chain xfade filters between consecutive segments.
    # xfade takes two video inputs and produces one output.
    # We chain: v0 xfade v1 -> xv0; xv0 xfade v2 -> xv1; etc.
    durations = [b - a for (a, b) in intervals]

    # Video crossfades
    prev_v = "[v0]"
    offset_acc = durations[0] - crossfade_s
    for i in range(1, n):
        out_label = f"[xv{i}]" if i < n - 1 else "[outv]"
        parts.append(
            f"{prev_v}[v{i}]xfade=transition=fade:"
            f"duration={crossfade_s:.3f}:offset={offset_acc:.3f}{out_label}"
        )
        prev_v = f"[xv{i}]"
        if i < n - 1:
            offset_acc += durations[i] - crossfade_s

    # Audio crossfades
    prev_a = "[a0]"
    offset_acc_a = durations[0] - crossfade_s
    for i in range(1, n):
        out_label = f"[xa{i}]" if i < n - 1 else "[outa]"
        parts.append(
            f"{prev_a}[a{i}]acrossfade=d={crossfade_s:.3f}:c1=tri:c2=tri{out_label}"
        )
        prev_a = f"[xa{i}]"
        if i < n - 1:
            offset_acc_a += durations[i] - crossfade_s

    filter_complex = ";".join(parts)

    cmd = [
        _ffmpeg_bin(),
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
        "-filter_complex",
        filter_complex,
        "-map",
        "[outv]",
        "-map",
        "[outa]",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "18",
        "-c:a",
        "aac",
        "-b:a",
        "256k",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return output_path

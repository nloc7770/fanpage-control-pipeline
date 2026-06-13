"""Zoom punches and pattern interrupts.

Both effects are expressed as ffmpeg ``-vf`` filter graphs. ``apply_zoom_punches``
piecewise scales the frame at given beats with an ease in-out ramp;
``apply_pattern_interrupts`` inserts short visual disruptions (flash, freeze,
cut-zoom).
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any


def _ffmpeg_bin() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def _probe_fps(input_path: str | Path, default: float = 30.0) -> float:
    """Best-effort fps probe for an input file. Falls back to ``default``."""
    try:
        from ffmpeg.probe import ffprobe_json

        info = ffprobe_json(input_path)
        for stream in info.get("streams", []) or []:
            if stream.get("codec_type") != "video":
                continue
            rate = stream.get("r_frame_rate") or stream.get("avg_frame_rate")
            if not rate or rate == "0/0":
                continue
            if "/" in str(rate):
                num, den = str(rate).split("/", 1)
                num_f, den_f = float(num), float(den)
                if den_f > 0:
                    return num_f / den_f
            return float(rate)
    except Exception:
        pass
    return default


def _build_zoom_expr_zoompan(
    beats: Sequence[dict[str, Any]], default_scale: float = 1.0
) -> str:
    """Build a ``zoompan`` ``z`` expression with an ease in-out per beat.

    zoompan exposes ``on`` (output frame number, 0-based) which is what we'd
    normally use, but timing is way easier if we just key off ``time``-style
    math via the input timeline. zoompan does NOT have ``t``, but it has
    ``in`` (current input frame) and ``in_time`` (input frame timestamp in
    seconds) in modern ffmpeg builds. We use ``in_time`` -- which is the
    timestamp of the current input frame in seconds since the start of the
    filter input.

    Each beat: ``{"at": float, "scale": float, "duration": float}``. Inside
    each beat window we smoothstep from ``default_scale`` up to ``scale`` then
    back. zoompan does NOT understand commas inside its z expression, but it
    does understand ``;`` and parentheses; we use only expression atoms
    documented in the ffmpeg expression eval (``if``, ``between``, ``abs``,
    ``min``, ``max``).
    """
    if not beats:
        return f"{default_scale:.4f}"

    expr = f"{default_scale:.4f}"
    for beat in reversed(beats):
        at = float(beat.get("at", 0.0))
        dur = max(0.05, float(beat.get("duration", 0.4)))
        target = float(beat.get("scale", 1.12))
        half = dur / 2.0
        mid = at + half
        # s_lin in [0,1], peaks at mid.
        s = f"max(0,min(1,1-abs((in_time-{mid:.4f})/{half:.4f})))"
        # smoothstep: 3s^2 - 2s^3
        ramp = (
            f"({default_scale:.4f}+({target:.4f}-{default_scale:.4f})*"
            f"(3*({s})*({s})-2*({s})*({s})*({s})))"
        )
        expr = f"if(between(in_time,{at:.3f},{at + dur:.3f}),{ramp},{expr})"
    return expr


def apply_zoom_punches(
    input_path: str | Path,
    output_path: str | Path,
    beats: Sequence[dict[str, Any]],
) -> Path:
    """Apply ease in-out zoom-in punches at the given beats using ``zoompan``.

    ``beats`` is a list of ``{"at": float, "scale": float, "duration": float}``
    where ``at`` and ``duration`` are seconds in the input timeline. We build a
    single ``zoompan`` filter with a time-varying ``z`` expression that ramps
    smoothly from 1.0 up to ``scale`` and back over the beat window. The
    ``d=1`` argument tells zoompan to emit one output frame per input frame
    (i.e. the input fps is preserved).
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not beats:
        # No-op: copy through.
        cmd = [
            _ffmpeg_bin(),
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(input_path),
            "-c",
            "copy",
            str(output_path),
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        return output_path

    z_expr = _build_zoom_expr_zoompan(beats)
    src_fps = _probe_fps(input_path)
    # zoompan parameters:
    #   z   : time-varying scale, our expression
    #   d   : duration in frames per output -- 1 = one frame per input frame
    #   s   : output size
    #   x,y : center the zoom on the frame middle
    #   fps : output fps -- match source so audio stays in sync
    vf = (
        f"zoompan=z='{z_expr}':d=1:s=1080x1920:"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':fps={src_fps:.4f}"
    )
    cmd = [
        _ffmpeg_bin(),
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "18",
        "-c:a",
        "copy",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return output_path


# Back-compat alias for callers that expect the older symbol name.
_build_zoom_expr = _build_zoom_expr_zoompan


def _build_zoomout_expr_zoompan(
    beats: Sequence[dict[str, Any]],
    *,
    rest_z: float,
    peak_z: float,
) -> str:
    """Build a zoompan ``z`` expression for zoom-OUT beats.

    Implementation note (zoompan + z<1 limitation)
    ----------------------------------------------
    ffmpeg's ``zoompan`` filter clamps ``z`` to ``>= 1.0`` -- values below 1
    silently render as 1, so a naive "1.0 -> 0.93 -> 1.0" ramp does nothing
    visible. Instead, we *upscale the source* by ``1/scale_to`` (handled by
    :func:`apply_zoom_out`) so the resting state is at ``z = 1/scale_to``
    (e.g. 1.075), and the beat peak ramps DOWN to ``z = 1.0`` -- at peak we
    show the FULL upscaled canvas (i.e. more content per frame -> reads as
    zoom-out to the viewer).

    Both ``rest_z`` and ``peak_z`` are >= 1.0 and ``rest_z >= peak_z``.
    """
    if not beats:
        return f"{rest_z:.4f}"

    expr = f"{rest_z:.4f}"
    for beat in reversed(beats):
        at = float(beat.get("at", 0.0))
        dur = max(0.05, float(beat.get("duration", 0.4)))
        half = dur / 2.0
        mid = at + half
        s = f"max(0,min(1,1-abs((in_time-{mid:.4f})/{half:.4f})))"
        # Smoothstep from rest_z down to peak_z and back.
        ramp = (
            f"({rest_z:.4f}+({peak_z:.4f}-{rest_z:.4f})*"
            f"(3*({s})*({s})-2*({s})*({s})*({s})))"
        )
        expr = f"if(between(in_time,{at:.3f},{at + dur:.3f}),{ramp},{expr})"
    return expr


def apply_zoom_out(
    input_path: str | Path,
    output_path: str | Path,
    beats: Sequence[dict[str, Any]],
    *,
    default_scale_to: float = 0.50,
    canvas_w: int = 1080,
    canvas_h: int = 1920,
) -> Path:
    """Apply zoom-OUT punches at the given beats.

    A "zoom-out" beat reveals more of the source frame at the peak, then
    returns to the resting framing. Visually it's the inverse of the existing
    :func:`apply_zoom_punches` and reads as "the camera breathing outward" --
    less aggressive than zoom-IN punches.

    Each beat: ``{"at": float, "duration": float, "scale_to": float}`` where
    ``scale_to`` is the peak shrink factor in (0, 1]. ``scale_to=0.93`` means
    the apparent framing zooms out to 93% (i.e. shows ~107% of the original
    field of view at the peak).

    Implementation: ffmpeg ``zoompan`` clamps z to >= 1.0, so we cannot
    directly ramp z from 1.0 down to 0.93. Instead, we *pre-upscale* the
    source by 1/scale_to (e.g. 107%) and run zoompan from z=1/scale_to
    (resting) down to z=1.0 (peak). The end-of-pipeline canvas size is
    ``canvas_w x canvas_h`` -- 1080x1920 by default.

    If ``beats`` contains heterogeneous ``scale_to`` values we use the
    *smallest* (most aggressive zoom-out) to pick the upscale factor so every
    beat fits. Per-beat scale is still honoured inside the z expression.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not beats:
        cmd = [
            _ffmpeg_bin(),
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(input_path),
            "-c",
            "copy",
            str(output_path),
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        return output_path

    # Pick the strongest (smallest) scale_to so the upscale covers every beat.
    scale_tos = [
        float(b.get("scale_to", b.get("scale", default_scale_to))) for b in beats
    ]
    strongest = min(scale_tos) if scale_tos else default_scale_to
    # Clamp to a sane range. <0.80 starts to look like a portal effect; >0.99
    # is invisible and would also produce upscale=1.01 which is a no-op.
    strongest = max(0.80, min(0.99, strongest))
    rest_z = 1.0 / strongest  # e.g. 1.0753 for 0.93

    # Normalize beats: each gets its own peak_z derived from its scale_to.
    norm_beats: list[dict[str, Any]] = []
    for b in beats:
        st = float(b.get("scale_to", b.get("scale", default_scale_to)))
        st = max(0.80, min(0.99, st))
        # The per-beat peak z relative to the upscaled source: we want the
        # viewer to see `st` of the framing at the peak. The upscale already
        # shows 1.0 at z=rest_z; at peak we go down to z = rest_z * st (which
        # equals 1.0 only if st == 1/rest_z == strongest -- otherwise it's
        # somewhere in [1.0, rest_z]).
        peak_z = rest_z * st
        if peak_z < 1.0:
            peak_z = 1.0
        norm_beats.append(
            {
                "at": float(b.get("at", 0.0)),
                "duration": float(b.get("duration", 0.4)),
                "peak_z": peak_z,
            }
        )

    # Build the z expression by hand (one beat at a time, since each beat has
    # its own peak_z).
    expr = f"{rest_z:.4f}"
    for beat in reversed(norm_beats):
        at = float(beat["at"])
        dur = max(0.05, float(beat["duration"]))
        peak_z = float(beat["peak_z"])
        half = dur / 2.0
        mid = at + half
        s = f"max(0,min(1,1-abs((in_time-{mid:.4f})/{half:.4f})))"
        ramp = (
            f"({rest_z:.4f}+({peak_z:.4f}-{rest_z:.4f})*"
            f"(3*({s})*({s})-2*({s})*({s})*({s})))"
        )
        expr = f"if(between(in_time,{at:.3f},{at + dur:.3f}),{ramp},{expr})"

    src_fps = _probe_fps(input_path)
    # Upscale source dims so zoompan z=rest_z reproduces the original framing.
    # Computed in integer pixel space to avoid odd-dim issues with H.264.
    up_w = int(round(canvas_w * rest_z))
    up_h = int(round(canvas_h * rest_z))
    # H.264 requires even dimensions.
    if up_w % 2:
        up_w += 1
    if up_h % 2:
        up_h += 1

    vf = (
        f"scale={up_w}:{up_h},"
        f"zoompan=z='{expr}':d=1:s={canvas_w}x{canvas_h}:"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':fps={src_fps:.4f}"
    )

    cmd = [
        _ffmpeg_bin(),
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "18",
        "-c:a",
        "copy",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return output_path


def apply_pattern_interrupts(
    input_path: str | Path,
    output_path: str | Path,
    interrupts: Sequence[dict[str, Any]],
) -> Path:
    """Apply pattern interrupts (flash / freeze / cut-zoom).

    Each interrupt: ``{"at": float, "kind": "flash"|"freeze"|"cut_zoom",
    "params": {...}}``. We build a single ``-vf`` chain that overlays a white
    frame for ``flash``, holds the previous frame for ``freeze``, and crops in
    for ``cut_zoom``. For Phase 2 we only implement ``flash`` since the other
    two need temporal filter graphs; the function still accepts the others as a
    no-op so the call site doesn't need to branch.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    parts: list[str] = []
    for itr in interrupts:
        kind = (itr.get("kind") or "").lower()
        if kind not in ("flash", "flash_cut"):
            continue
        at = float(itr.get("at", 0.0))
        params = itr.get("params") or {}
        dur_ms = params.get("duration_ms")
        dur = float(dur_ms) / 1000.0 if dur_ms else float(params.get("duration", 0.08))
        dur = max(0.04, min(0.25, dur))
        parts.append(
            f"drawbox=enable='between(t\\,{at:.3f}\\,{at + dur:.3f})':"
            "x=0:y=0:w=iw:h=ih:color=white@0.85:t=fill"
        )
    if not parts:
        # Nothing to do -- copy through unchanged.
        cmd = [
            _ffmpeg_bin(),
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(input_path),
            "-c",
            "copy",
            str(output_path),
        ]
    else:
        vf = ",".join(parts)
        cmd = [
            _ffmpeg_bin(),
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(input_path),
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "18",
            "-c:a",
            "copy",
            str(output_path),
        ]
    subprocess.run(cmd, check=True, capture_output=True)
    return output_path

"""ffmpeg + OpenCV operations wrapper.

Public surface used by the render worker. Each helper shells out to the
``ffmpeg`` binary (we deliberately avoid ``ffmpeg-python``'s heavy stream
graph DSL because the render plan is short and explicit). Progress is reported
via callbacks; see :mod:`ffmpeg.progress`.
"""

from __future__ import annotations

from ffmpeg.audio import (
    compute_speech_intervals,
    detect_silence,
    mute_audio,
    remap_time,
    replace_audio,
    total_kept,
)
from ffmpeg.crop import crop_to_9_16
from ffmpeg.cut import cut_concat, cut_segment
from ffmpeg.effects import (
    apply_pattern_interrupts,
    apply_zoom_out,
    apply_zoom_punches,
)
from ffmpeg.layouts import apply_layout, apply_tweet_layout
from ffmpeg.pipeline import render_clip
from ffmpeg.probe import ffprobe_json, get_duration_s
from ffmpeg.progress import parse_progress_lines
from ffmpeg.subtitles import (
    build_ass,
    build_ass_from_narrative,
    build_ass_words,
    burn_ass,
)

__all__ = [
    "apply_layout",
    "apply_pattern_interrupts",
    "apply_tweet_layout",
    "apply_zoom_out",
    "apply_zoom_punches",
    "build_ass",
    "build_ass_from_narrative",
    "build_ass_words",
    "burn_ass",
    "compute_speech_intervals",
    "crop_to_9_16",
    "cut_concat",
    "cut_segment",
    "detect_silence",
    "ffprobe_json",
    "get_duration_s",
    "mute_audio",
    "parse_progress_lines",
    "remap_time",
    "render_clip",
    "replace_audio",
    "total_kept",
]

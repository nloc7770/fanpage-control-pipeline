"""Hook text overlay for the first few seconds of a clip.

Burns a styled text banner at the top of the clip to grab viewer attention.
Uses ffmpeg drawtext with fade-in/hold/fade-out timing.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


def _ffmpeg_bin() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def _escape_drawtext_file(text: str) -> str:
    """Escape text for ffmpeg drawtext textfile (less escaping needed)."""
    text = text.replace("\n", " ")
    return text


def apply_hook_text(
    input_path: str | Path,
    output_path: str | Path,
    hook_text: str,
    *,
    font_size: int = 60,
    fade_in: float = 0.3,
    hold_duration: float = 2.5,
    fade_out: float = 0.3,
    y_position: str = "h*0.30",
    canvas_w: int = 1080,
) -> Path:
    """Burn a hook text overlay at the start of the clip.

    The text appears as a white bold label on a dark semi-transparent
    background pill, centered horizontally at ~30% from the top.

    Parameters
    ----------
    input_path:
        Source video.
    output_path:
        Destination video with overlay burned in.
    hook_text:
        The text to display. Truncated to ~60 chars if too long.
    font_size:
        Font size in pixels (default 60).
    fade_in:
        Fade-in duration in seconds.
    hold_duration:
        How long the text stays fully visible.
    fade_out:
        Fade-out duration in seconds.
    y_position:
        Vertical position expression for ffmpeg (default "h*0.30").
    canvas_w:
        Canvas width for padding calculations.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not hook_text or not hook_text.strip():
        # No text to burn -- copy through.
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

    # Truncate overly long hooks to avoid text overflow
    text = hook_text.strip()
    if len(text) > 60:
        text = text[:57] + "..."

    text = _escape_drawtext_file(text)
    total_duration = fade_in + hold_duration + fade_out  # 3.1s total

    # Alpha expression: fade in from 0-0.3s, hold 0.3-2.8s, fade out 2.8-3.1s
    t_fade_in_end = fade_in
    t_fade_out_start = fade_in + hold_duration
    t_end = total_duration

    alpha_expr = (
        f"if(lt(t\\,{t_fade_in_end:.2f})\\,t/{fade_in:.2f}\\,"
        f"if(lt(t\\,{t_fade_out_start:.2f})\\,1\\,"
        f"if(lt(t\\,{t_end:.2f})\\,({t_end:.2f}-t)/{fade_out:.2f}\\,0)))"
    )

    # Write text to a temp file to avoid shell/ffmpeg escaping issues with Unicode
    text_file = tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    )
    text_file.write(text)
    text_file.close()
    text_file_path = text_file.name

    # Background pill: dark semi-transparent box behind text
    padding_x = 24
    padding_y = 12

    drawtext_filter = (
        f"drawtext="
        f"textfile=’{text_file_path}’:"
        f"fontsize={font_size}:"
        f"fontcolor=white:"
        f"x=(w-text_w)/2:"
        f"y={y_position}:"
        f"borderw=2:"
        f"bordercolor=black@0.4:"
        f"box=1:"
        f"boxcolor=black@0.55:"
        f"boxborderw={padding_x}|{padding_y}|{padding_x}|{padding_y}:"
        f"alpha=’{alpha_expr}’:"
        f"enable=’between(t,0,{t_end:.2f})’"
    )

    cmd = [
        _ffmpeg_bin(),
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
        "-vf",
        drawtext_filter,
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
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    finally:
        Path(text_file_path).unlink(missing_ok=True)
    return output_path

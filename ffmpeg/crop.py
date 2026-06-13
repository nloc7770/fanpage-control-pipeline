"""9:16 fit + blurred-background composer.

Source landscape video → 9:16 portrait by:
  1. Foreground: scale source to fit canvas WIDTH (1080), placed centered.
  2. Background: scale source to FILL canvas (1080×1920) cropped, then heavy
     blur. Fills the otherwise-empty top/bottom bands.
  3. Compose foreground over background.

This preserves the FULL source frame (no horizontal crop) so subjects on the
edges aren't lost. ``focus_track`` is accepted for API compatibility but
ignored — there's no horizontal crop to steer.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any


def _ffmpeg_bin() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def crop_to_9_16(
    input_path: str | Path,
    output_path: str | Path,
    focus_track: Sequence[dict[str, Any]] | None = None,  # noqa: ARG001 - kept for API compat
    *,
    target_height: int = 1920,
    blur_strength: int = 20,
) -> Path:
    """Compose ``input_path`` into a 9:16 frame with a blurred background.

    The output canvas is ``(target_height * 9 // 16) × target_height``
    (default 1080×1920). The foreground is the full source scaled to canvas
    width and centred vertically; the background is the source scaled to
    fill + boxblur, occupying the otherwise-empty top/bottom bands.
    """
    target_width = target_height * 9 // 16

    vf = (
        "split=2[fg][bg];"
        f"[bg]scale={target_width}:{target_height}"
        ":force_original_aspect_ratio=increase,"
        f"crop={target_width}:{target_height},"
        f"boxblur={blur_strength}:1[bg_blur];"
        f"[fg]scale={target_width}:-2[fg_scaled];"
        "[bg_blur][fg_scaled]overlay=(W-w)/2:(H-h)/2:format=auto,setsar=1"
    )

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        _ffmpeg_bin(),
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
        "-filter_complex",
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

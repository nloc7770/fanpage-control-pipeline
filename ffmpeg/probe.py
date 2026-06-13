"""ffprobe helpers."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


def _ffprobe_bin() -> str:
    binary = shutil.which("ffprobe") or "ffprobe"
    return binary


def ffprobe_json(path: str | Path) -> dict[str, Any]:
    """Return the full JSON description of the media file at ``path``.

    Output contains a ``format`` object and a ``streams`` array, identical to
    ``ffprobe -print_format json -show_format -show_streams``.
    """
    cmd = [
        _ffprobe_bin(),
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(proc.stdout)


def get_duration_s(path: str | Path) -> float:
    """Return the media duration in seconds. Falls back to 0.0 if not present."""
    info = ffprobe_json(path)
    fmt = info.get("format") or {}
    try:
        return float(fmt.get("duration", 0.0))
    except (TypeError, ValueError):
        return 0.0

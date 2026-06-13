#!/usr/bin/env python3
"""Quick smoke test for ``services.tts.runner.synthesize_vi``.

Usage:
    PYTHONPATH=. python3 scripts/tts_smoke.py "Xin chào, đây là một bản tin ngắn."

Writes ``/tmp/tts_smoke.wav`` and prints the duration (seconds) of the output.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path


def _duration_seconds(wav_path: Path) -> float:
    out = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(wav_path),
        ],
        text=True,
    )
    return float(out.strip())


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: tts_smoke.py '<vietnamese text>'", file=sys.stderr)
        return 2
    text = argv[1]
    out_path = Path("/tmp/tts_smoke.wav")

    from services.tts.runner import synthesize_vi

    t0 = time.perf_counter()
    result = synthesize_vi(text, out_path)
    elapsed = time.perf_counter() - t0

    dur = _duration_seconds(result)
    rtf = elapsed / dur if dur > 0 else float("inf")
    print(f"output: {result}")
    print(f"chars: {len(text)}")
    print(f"wall_time_s: {elapsed:.3f}")
    print(f"duration_s: {dur:.3f}")
    print(f"realtime_factor: {rtf:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

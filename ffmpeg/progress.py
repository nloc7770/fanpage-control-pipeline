"""Parse ffmpeg's ``-progress pipe:1`` output stream.

ffmpeg emits key=value lines and a final ``progress=continue|end``. We feed
chunks from stdout into :func:`parse_progress_lines` and yield ``(key, value)``
pairs; callers convert ``out_time_ms`` into a percentage given the total
duration.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator


def parse_progress_lines(lines: Iterable[str]) -> Iterator[tuple[str, str]]:
    """Yield ``(key, value)`` pairs from ffmpeg progress output."""
    for raw in lines:
        line = raw.strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        yield key.strip(), value.strip()


def progress_callback_factory(
    duration_s: float,
    on_pct: Callable[[float], None],
) -> Callable[[Iterable[str]], None]:
    """Build a consumer that pumps ffmpeg progress lines into ``on_pct``."""

    def consume(lines: Iterable[str]) -> None:
        for key, value in parse_progress_lines(lines):
            if key == "out_time_ms":
                try:
                    secs = float(value) / 1_000_000.0
                except ValueError:
                    continue
                if duration_s > 0:
                    pct = min(100.0, max(0.0, (secs / duration_s) * 100.0))
                    on_pct(pct)
            elif key == "progress" and value == "end":
                on_pct(100.0)

    return consume

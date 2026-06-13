"""pyannote-based speaker diarization."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from loguru import logger


@dataclass(slots=True)
class SpeakerTurn:
    """A single ``[start, end]`` window attributed to one speaker."""

    speaker_id: str
    start: float
    end: float


def diarize(audio_path: str | Path) -> list[SpeakerTurn]:
    """Return per-speaker turns.

    Honors three env knobs:

    * ``ENABLE_DIARIZATION=0`` -- always return empty (no diarization).
    * ``MOCK_ASR=1`` -- treat the upstream stage as mocked and return a stub
      two-speaker layout to exercise the merge logic.
    * neither set -- run real pyannote.audio.
    """
    if os.environ.get("ENABLE_DIARIZATION", "1") == "0":
        logger.info("diarization disabled (ENABLE_DIARIZATION=0)")
        return []

    if os.environ.get("MOCK_ASR", "0") == "1":
        return _mock_turns()

    token = os.environ.get("HUGGINGFACE_TOKEN")
    if not token:
        logger.warning(
            "HUGGINGFACE_TOKEN not set; falling back to mock diarization output"
        )
        return _mock_turns()

    try:
        from pyannote.audio import Pipeline  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - install-time error
        raise RuntimeError(
            "pyannote.audio not installed. Install in GPU worker image "
            "or set ENABLE_DIARIZATION=0 / MOCK_ASR=1 for dev mode."
        ) from exc

    pipeline = Pipeline.from_pretrained(
        "pyannote/speaker-diarization-3.1",
        use_auth_token=token,
    )

    annotation = pipeline(str(audio_path))
    turns: list[SpeakerTurn] = []
    for segment, _, speaker in annotation.itertracks(yield_label=True):
        turns.append(
            SpeakerTurn(
                speaker_id=str(speaker),
                start=float(segment.start),
                end=float(segment.end),
            )
        )
    logger.info("diarization: produced {} turns", len(turns))
    return turns


def _mock_turns() -> list[SpeakerTurn]:
    """Two alternating speakers over a 600-second window."""
    turns: list[SpeakerTurn] = []
    speaker_a, speaker_b = "SPEAKER_00", "SPEAKER_01"
    cursor = 0.0
    flip = False
    while cursor < 600.0:
        nxt = min(600.0, cursor + 20.0)
        turns.append(
            SpeakerTurn(
                speaker_id=speaker_b if flip else speaker_a,
                start=cursor,
                end=nxt,
            )
        )
        cursor = nxt
        flip = not flip
    return turns

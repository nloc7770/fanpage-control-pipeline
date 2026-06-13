"""WhisperX runner.

Lazy-imports ``whisperx`` so the worker image only pays the cost when ASR is
actually executed. The mock path returns a deterministic, plausible-looking
transcript that the rest of the pipeline can validate against.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass(slots=True)
class TranscriptResult:
    """WhisperX transcript output.

    ``segments`` holds sentence-level chunks (``{"start", "end", "text"}``);
    ``words`` holds word-level alignments (``{"start", "end", "word"}``).
    """

    language: str
    segments: list[dict[str, Any]]
    words: list[dict[str, Any]]
    extras: dict[str, Any] = field(default_factory=dict)


def transcribe(audio_path: str | Path, *, language_hint: str | None = None) -> TranscriptResult:
    """Transcribe ``audio_path``. Honors ``MOCK_ASR``."""
    if os.environ.get("MOCK_ASR", "0") == "1":
        return _mock_transcript(language_hint=language_hint)

    # NB: this file lives in a package also named whisperx
    # (services/whisperx/), which shadows the upstream library on sys.path.
    # Load the upstream module by file spec so import whisperx here does
    # not resolve back to ourselves.
    try:
        import importlib, importlib.util, sys
        if "whisperx" in sys.modules and not hasattr(sys.modules["whisperx"], "load_model"):
            del sys.modules["whisperx"]
        spec = None
        for site_dir in ("/usr/local/lib/python3.11/dist-packages", "/usr/lib/python3/dist-packages"):
            cand = f"{site_dir}/whisperx/__init__.py"
            try:
                spec = importlib.util.spec_from_file_location("whisperx", cand, submodule_search_locations=[f"{site_dir}/whisperx"])
                if spec is not None:
                    break
            except Exception:
                continue
        if spec is None:
            whisperx = importlib.import_module("whisperx")  # type: ignore[import-untyped]
        else:
            whisperx = importlib.util.module_from_spec(spec)
            sys.modules["whisperx"] = whisperx
            spec.loader.exec_module(whisperx)  # type: ignore[union-attr]
    except ImportError as exc:  # pragma: no cover - install-time error
        raise RuntimeError(
            "whisperx is not installed. Install in the GPU worker image "
            "or set MOCK_ASR=1 for dev mode."
        ) from exc

    device = os.environ.get("WHISPERX_DEVICE", "cuda")
    model_name = os.environ.get("WHISPERX_MODEL", "large-v3")
    compute_type = os.environ.get("WHISPERX_COMPUTE_TYPE", "float16")
    batch_size = int(os.environ.get("WHISPERX_BATCH_SIZE", "16"))

    logger.info(
        "whisperx: loading model={} device={} compute={}", model_name, device, compute_type
    )
    model = whisperx.load_model(model_name, device, compute_type=compute_type)
    audio = whisperx.load_audio(str(audio_path))
    result = model.transcribe(audio, batch_size=batch_size, language=language_hint)

    language = result.get("language") or language_hint or "en"

    # Force-align to word level.
    try:
        align_model, metadata = whisperx.load_align_model(
            language_code=language, device=device
        )
        aligned = whisperx.align(
            result["segments"],
            align_model,
            metadata,
            audio,
            device,
            return_char_alignments=False,
        )
        segments = aligned.get("segments", result["segments"])
        words: list[dict[str, Any]] = []
        for seg in segments:
            for w in seg.get("words", []) or []:
                if "start" in w and "end" in w:
                    words.append(
                        {"start": float(w["start"]), "end": float(w["end"]), "word": w["word"]}
                    )
    except Exception as exc:
        logger.warning("whisperx alignment failed ({}); returning sentence-level only", exc)
        segments = result["segments"]
        words = []

    return TranscriptResult(language=language, segments=segments, words=words)


# ---------------------------------------------------------------------------
# Mock
# ---------------------------------------------------------------------------


_MOCK_WORDS = [
    "this",
    "is",
    "a",
    "mock",
    "transcript",
    "for",
    "the",
    "shortform",
    "factory",
    "pipeline",
    "and",
    "it",
    "is",
    "deterministic",
    "by",
    "design",
]


def _mock_transcript(*, language_hint: str | None = None) -> TranscriptResult:
    """Build a 600-second mock transcript with stable word boundaries."""
    duration_s = 600.0
    words_per_segment = 8
    word_step = duration_s / 400.0  # ~400 words across 10 min
    words: list[dict[str, Any]] = []
    segments: list[dict[str, Any]] = []

    for i in range(400):
        token = _MOCK_WORDS[i % len(_MOCK_WORDS)]
        start = i * word_step
        end = start + word_step * 0.9
        words.append({"start": start, "end": end, "word": token})

    for s_idx in range(0, 400, words_per_segment):
        chunk = words[s_idx : s_idx + words_per_segment]
        if not chunk:
            continue
        segments.append(
            {
                "start": chunk[0]["start"],
                "end": chunk[-1]["end"],
                "text": " ".join(w["word"] for w in chunk),
                "words": chunk,
            }
        )

    return TranscriptResult(
        language=language_hint or "en",
        segments=segments,
        words=words,
        extras={"mock": True},
    )

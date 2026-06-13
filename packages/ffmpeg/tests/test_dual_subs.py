"""Tests for the dual-language (EN bottom + VI top) subtitle builder."""

from __future__ import annotations

import re

import pytest

from ffmpeg.subtitles import (
    build_ass_dual,
    build_segments_from_clip_words,
    build_segments_from_words,
)


# ---------------------------------------------------------------------------
# build_ass_dual
# ---------------------------------------------------------------------------


def _parse_styles(ass: str) -> dict[str, dict[str, str]]:
    """Return a {style_name: {field: value}} mapping from an ASS document."""
    fmt_line = next(
        (l for l in ass.splitlines() if l.startswith("Format:") and "Alignment" in l),
        None,
    )
    assert fmt_line is not None, "no [V4+ Styles] Format line found"
    fields = [f.strip() for f in fmt_line.split(":", 1)[1].split(",")]
    out: dict[str, dict[str, str]] = {}
    for line in ass.splitlines():
        if not line.startswith("Style:"):
            continue
        values = [v.strip() for v in line.split(":", 1)[1].split(",")]
        if len(values) != len(fields):
            continue
        d = dict(zip(fields, values))
        out[d["Name"]] = d
    return out


def _parse_dialogues(ass: str) -> list[dict[str, str]]:
    fmt_line = next(
        (l for l in ass.splitlines() if l.startswith("Format:") and "Text" in l),
        None,
    )
    assert fmt_line is not None
    fields = [f.strip() for f in fmt_line.split(":", 1)[1].split(",")]
    n_lead = len(fields) - 1  # everything before Text
    out: list[dict[str, str]] = []
    for line in ass.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        body = line.split(":", 1)[1].lstrip()
        # Text is the last field but may contain commas; split lead-fields only.
        parts = body.split(",", n_lead)
        if len(parts) != len(fields):
            continue
        out.append(dict(zip(fields, [p.strip() for p in parts])))
    return out


def test_build_ass_dual_emits_two_styles_with_correct_alignment():
    en_segments = [
        {"start": 0.0, "end": 1.5, "text": "Hello world this is a test"},
        {"start": 1.5, "end": 3.0, "text": "second english chunk"},
    ]
    vi_segments = [
        {"start": 0.0, "end": 1.5, "text": "Xin chào thế giới đây là một bài kiểm tra"},
        {"start": 1.5, "end": 3.0, "text": "đoạn tiếng Việt thứ hai"},
    ]
    ass = build_ass_dual(en_segments, vi_segments, total_duration_s=3.0)

    styles = _parse_styles(ass)
    assert "BottomEN" in styles, "BottomEN style missing"
    assert "TopVI" in styles, "TopVI style missing"
    # Alignment: both lines anchor to bottom-center (ASS numpad 2). VI sits
    # directly above EN via a larger MarginV so the two lines stay visually
    # close together.
    assert styles["BottomEN"]["Alignment"] == "2"
    assert styles["TopVI"]["Alignment"] == "2"


def test_build_ass_dual_dialogue_references_both_styles():
    en_segments = [{"start": 0.0, "end": 1.0, "text": "hi"}]
    vi_segments = [{"start": 0.0, "end": 1.0, "text": "xin chào"}]
    ass = build_ass_dual(en_segments, vi_segments, total_duration_s=1.0)
    dialogues = _parse_dialogues(ass)
    styles_used = {d["Style"] for d in dialogues}
    assert "BottomEN" in styles_used
    assert "TopVI" in styles_used
    # >= input segments count (chunking may add).
    assert len(dialogues) >= len(en_segments) + len(vi_segments)


def test_build_ass_dual_chunks_long_segments():
    # Long EN segment (>5 words and >30 chars) should be split.
    en_segments = [
        {
            "start": 0.0,
            "end": 5.0,
            "text": "this is a deliberately long english sentence with more than five words",
        }
    ]
    vi_segments = [
        {
            "start": 0.0,
            "end": 5.0,
            "text": "đây là một câu tiếng Việt dài cố ý nhiều hơn năm từ",
        }
    ]
    ass = build_ass_dual(en_segments, vi_segments, total_duration_s=5.0)
    dialogues = _parse_dialogues(ass)
    en_lines = [d for d in dialogues if d["Style"] == "BottomEN"]
    vi_lines = [d for d in dialogues if d["Style"] == "TopVI"]
    # Each long input should have produced more than one dialogue line.
    assert len(en_lines) >= 2
    assert len(vi_lines) >= 2
    # No dialogue text should exceed 5 words.
    for d in dialogues:
        word_count = len(d["Text"].split())
        assert word_count <= 5, f"chunk too long ({word_count} words): {d['Text']!r}"


def test_build_ass_dual_strips_karaoke_tags():
    en_segments = [
        {
            "start": 0.0,
            "end": 1.0,
            "text": r"{\kf30}hello{\kf20} world",
        }
    ]
    ass = build_ass_dual(en_segments, [], total_duration_s=1.0)
    # The karaoke \k tags should be gone.
    assert "\\kf" not in ass
    assert "\\k20" not in ass
    assert "hello" in ass and "world" in ass


def test_build_ass_dual_empty_inputs_yields_only_header():
    ass = build_ass_dual([], [], total_duration_s=3.0)
    assert "[V4+ Styles]" in ass
    assert "BottomEN" in ass and "TopVI" in ass
    # No Dialogue lines.
    dialogues = _parse_dialogues(ass)
    assert dialogues == []


def test_build_ass_dual_style_overrides():
    style = {
        "font": "Roboto",
        "en_size": 72,
        "vi_size": 48,
        "en_primary_color": "#FF0000",  # red
        "vi_primary_color": "#00FF00",  # green
    }
    ass = build_ass_dual(
        [{"start": 0.0, "end": 1.0, "text": "x"}],
        [{"start": 0.0, "end": 1.0, "text": "y"}],
        total_duration_s=1.0,
        style=style,
    )
    styles = _parse_styles(ass)
    assert styles["BottomEN"]["Fontname"] == "Roboto"
    assert styles["BottomEN"]["Fontsize"] == "72"
    assert styles["TopVI"]["Fontsize"] == "48"
    # ASS color is &HAABBGGRR. Red #FF0000 -> &H000000FF.
    assert styles["BottomEN"]["PrimaryColour"].upper() == "&H000000FF"
    # Green #00FF00 -> &H0000FF00.
    assert styles["TopVI"]["PrimaryColour"].upper() == "&H0000FF00"


# ---------------------------------------------------------------------------
# build_segments_from_words / build_segments_from_clip_words
# ---------------------------------------------------------------------------


def _make_words(spec: list[tuple[str, float, float]]) -> list[dict]:
    return [{"word": w, "start": s, "end": e} for (w, s, e) in spec]


def test_build_segments_from_words_respects_max_words_per_chunk():
    # 10 contiguous source words from t=0 -> t=5, all inside the clip window
    # [0, 10] and no silence intervals.
    words = _make_words([(f"w{i}", i * 0.5, i * 0.5 + 0.4) for i in range(10)])
    segments = build_segments_from_words(
        words, clip_start=0.0, clip_end=10.0, intervals=[(0.0, 10.0)]
    )
    assert segments, "expected at least one segment"
    for seg in segments:
        assert len(seg["text"].split()) <= 5, f"chunk too wide: {seg}"


def test_build_segments_from_words_breaks_on_large_gap():
    # Two clusters with a 1s gap between them -- should produce 2+ chunks
    # because max_gap_s defaults to 0.5.
    words = _make_words(
        [
            ("a", 0.0, 0.3),
            ("b", 0.3, 0.6),
            ("c", 0.6, 0.9),
            # gap of 1.0s here:
            ("d", 1.9, 2.2),
            ("e", 2.2, 2.5),
        ]
    )
    segments = build_segments_from_words(
        words, clip_start=0.0, clip_end=5.0, intervals=[(0.0, 5.0)]
    )
    assert len(segments) >= 2
    # Verify the gap actually broke the chunk: first chunk should end before
    # the gap starts, second chunk should start after.
    assert segments[0]["end"] <= 1.0
    assert segments[1]["start"] >= 1.5


def test_build_segments_from_words_no_internal_gap_too_large():
    # Same gap rule, but reading from the inside: within ANY emitted chunk,
    # adjacent words should be within max_gap_s of each other.
    words = _make_words(
        [
            ("a", 0.0, 0.3),
            ("b", 0.4, 0.7),
            ("c", 0.8, 1.1),
            # 0.6s gap -> next chunk
            ("d", 1.7, 2.0),
            ("e", 2.0, 2.3),
        ]
    )
    segments = build_segments_from_words(
        words, clip_start=0.0, clip_end=5.0, intervals=[(0.0, 5.0)]
    )
    # Re-derive the source words inside each chunk by overlap with chunk span.
    for seg in segments:
        in_chunk = [
            w for w in words if w["start"] >= seg["start"] - 1e-6 and w["end"] <= seg["end"] + 1e-6
        ]
        for prev, nxt in zip(in_chunk, in_chunk[1:]):
            assert nxt["start"] - prev["end"] <= 0.5 + 1e-6, (
                f"chunk {seg!r} has an internal gap > 0.5s: {prev} -> {nxt}"
            )


def test_build_segments_from_words_filters_outside_clip_window():
    words = _make_words(
        [
            ("before", -1.0, -0.5),  # before clip
            ("inside", 1.0, 1.4),
            ("inside2", 1.5, 1.9),
            ("after", 12.0, 12.5),  # after clip
        ]
    )
    segments = build_segments_from_words(
        words, clip_start=0.0, clip_end=10.0, intervals=[(0.0, 10.0)]
    )
    joined = " ".join(s["text"] for s in segments)
    assert "before" not in joined
    assert "after" not in joined
    assert "inside" in joined


def test_build_segments_from_words_respects_silence_intervals():
    # Words across [0, 4]. Silence-tighten keeps only [0, 1] and [3, 4].
    words = _make_words(
        [
            ("a", 0.1, 0.4),
            ("b", 0.5, 0.8),
            ("dead", 1.5, 1.9),  # falls in the removed silence -> dropped
            ("c", 3.1, 3.4),
        ]
    )
    intervals = [(0.0, 1.0), (3.0, 4.0)]
    segments = build_segments_from_words(
        words, clip_start=0.0, clip_end=4.0, intervals=intervals
    )
    joined = " ".join(s["text"] for s in segments)
    assert "dead" not in joined
    assert "a" in joined and "b" in joined and "c" in joined
    # After remap, word "c" originally at 3.1s should sit at ~1.1s on the
    # tightened timeline (1.0s kept from [0,1] + 0.1s into [3,4]). The
    # containing chunk's end should reach there.
    c_chunk = next(s for s in segments if "c" in s["text"].split())
    assert 1.0 <= c_chunk["end"] <= 1.5


def test_build_segments_from_clip_words_basic():
    # Already-remapped clip-time words -- this is what the pipeline uses
    # because it has already done the highlight + silence remap.
    clip_words = [
        {"word": "hello", "start": 0.0, "end": 0.4},
        {"word": "there", "start": 0.4, "end": 0.8},
        {"word": "world", "start": 0.8, "end": 1.2},
    ]
    segments = build_segments_from_clip_words(clip_words)
    assert len(segments) == 1
    seg = segments[0]
    assert seg["text"].split() == ["hello", "there", "world"]
    assert seg["start"] == pytest.approx(0.0)
    assert seg["end"] == pytest.approx(1.2)


def test_build_ass_dual_sample_for_visual_inspection(capsys):
    """Print a small sample for the final report -- not a real assertion."""
    ass = build_ass_dual(
        [
            {"start": 0.0, "end": 1.0, "text": "Hello world"},
            {"start": 1.0, "end": 2.0, "text": "second line"},
        ],
        [
            {"start": 0.0, "end": 1.0, "text": "Xin chào thế giới"},
            {"start": 1.0, "end": 2.0, "text": "dòng thứ hai"},
        ],
        total_duration_s=2.0,
    )
    dialogues = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
    # Five dialogue lines expected: 2 EN + 2 VI = 4 (no chunking needed).
    assert len(dialogues) == 4
    print("\n--- sample ASS dialogues ---")
    for d in dialogues:
        print(d)
    print("--- /sample ---")

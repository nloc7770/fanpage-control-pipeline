"""Unit tests for ``services.qwen.runner`` -- focused on the segment-aware
narrative pipeline added when the Vietnamese TTS pipeline gained
``narrative_segments``.

The full Qwen HTTP call is mocked via a fake client so these tests run in
milliseconds with no network.
"""

from __future__ import annotations

from typing import Any

import pytest


class _FakeQwenClient:
    """Stand-in for ``ai.qwen_client.QwenClient`` that returns a canned plan."""

    def __init__(self, response_plan: Any) -> None:
        self._response = response_plan
        self.calls: list[Any] = []
        self.closed = False

    def chat_json(self, messages: list[dict[str, str]], schema: Any, **_: Any) -> Any:
        self.calls.append(messages)
        # The runner expects an EditPlan instance; return one verbatim.
        return self._response

    def close(self) -> None:
        self.closed = True

    # context-manager compat (not used by the runner, but mirrors QwenClient).
    def __enter__(self) -> "_FakeQwenClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


def _make_plan(**overrides: Any) -> Any:
    from shared_py.llm_contracts import (
        CropPlan,
        EditingStyle,
        EditPlan,
        SubtitleStyle,
    )

    defaults = dict(
        clip_index=0,
        title="Tiêu đề mẫu",
        hook="Hook",
        summary="Tóm tắt",
        viral_angle="curiosity",
        editing_style=EditingStyle(),
        narrative_script_vi=None,
        narrative_segments=None,
        visual_effects=[],
        subtitle_style=SubtitleStyle(),
        pattern_interrupts=[],
        crop_plan=CropPlan(),
    )
    defaults.update(overrides)
    return EditPlan(**defaults)


def test_plan_edit_passes_narrative_segments_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """When Qwen already returns narrative_segments, they survive normalisation."""
    from shared_py.llm_contracts import NarrativeSegment

    from services.qwen import runner

    response = _make_plan(
        narrative_script_vi=None,
        narrative_segments=[
            NarrativeSegment(start=0.0, end=5.0, text_vi="Đoạn một."),
            NarrativeSegment(start=5.0, end=12.0, text_vi="Đoạn hai dài hơn."),
            NarrativeSegment(start=12.0, end=20.0, text_vi="Đoạn ba kết thúc."),
        ],
    )
    fake = _FakeQwenClient(response)

    clip = {
        "clip_index": 0,
        "start_time": 100.0,
        "end_time": 120.0,
        "duration": 20.0,
    }
    transcript = [
        {"start": 100.0, "end": 105.0, "text": "First English sentence."},
        {"start": 105.0, "end": 112.0, "text": "Second one is longer."},
        {"start": 112.0, "end": 120.0, "text": "Third wraps it up."},
    ]

    out = runner.plan_edit(clip, transcript, client=fake)
    assert out.narrative_segments is not None
    assert len(out.narrative_segments) == 3
    assert [round(s.start, 1) for s in out.narrative_segments] == [0.0, 5.0, 12.0]
    assert [round(s.end, 1) for s in out.narrative_segments] == [5.0, 12.0, 20.0]
    # Joined narrative is synthesised from the segments.
    assert out.narrative_script_vi
    assert "Đoạn một." in out.narrative_script_vi
    assert "Đoạn ba" in out.narrative_script_vi


def test_plan_edit_normalises_legacy_only_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """If Qwen only returns narrative_script_vi, segments are auto-derived."""
    from services.qwen import runner

    response = _make_plan(
        narrative_script_vi=(
            "Câu mở đầu thật ấn tượng. Tiếp theo là chi tiết bất ngờ. "
            "Và cuối cùng là cú twist khép lại."
        ),
        narrative_segments=None,
    )
    fake = _FakeQwenClient(response)

    clip = {
        "clip_index": 0,
        "start_time": 0.0,
        "end_time": 30.0,
        "duration": 30.0,
    }
    transcript = [
        {"start": 0.0, "end": 8.0, "text": "Sentence one."},
        {"start": 8.0, "end": 20.0, "text": "Sentence two is longer."},
        {"start": 20.0, "end": 30.0, "text": "Sentence three closes it."},
    ]

    out = runner.plan_edit(clip, transcript, client=fake)
    assert out.narrative_segments is not None
    assert len(out.narrative_segments) == 3
    # Times are relative to clip and stay within [0, clip_duration].
    for seg in out.narrative_segments:
        assert seg.start >= 0.0
        assert seg.end <= 30.0
        assert seg.start < seg.end
        assert seg.text_vi.strip()
    # First segment starts at 0 (clip start), last ends at clip end.
    assert out.narrative_segments[0].start == pytest.approx(0.0)
    assert out.narrative_segments[-1].end == pytest.approx(30.0)


def test_plan_edit_clamps_out_of_range_segments(monkeypatch: pytest.MonkeyPatch) -> None:
    """Segments with start/end outside [0, clip_duration] get clamped or dropped."""
    from shared_py.llm_contracts import NarrativeSegment

    from services.qwen import runner

    response = _make_plan(
        narrative_script_vi=None,
        narrative_segments=[
            NarrativeSegment(start=0.0, end=5.0, text_vi="OK."),
            # End past clip duration -- should clamp to clip_duration.
            NarrativeSegment(start=5.0, end=999.0, text_vi="Dài quá."),
            # Empty text -- should be dropped.
            NarrativeSegment(start=10.0, end=11.0, text_vi=""),
        ],
    )
    fake = _FakeQwenClient(response)

    clip = {
        "clip_index": 0,
        "start_time": 0.0,
        "end_time": 15.0,
        "duration": 15.0,
    }
    transcript = [
        {"start": 0.0, "end": 5.0, "text": "Hi."},
        {"start": 5.0, "end": 15.0, "text": "Bye."},
    ]

    out = runner.plan_edit(clip, transcript, client=fake)
    assert out.narrative_segments is not None
    # Empty-text segment dropped, leaving 2.
    assert len(out.narrative_segments) == 2
    # Second segment clamped to clip_duration.
    assert out.narrative_segments[-1].end <= 15.0


def test_ensure_segment_window_handles_word_level_input() -> None:
    """Word-level transcript gets grouped into sentence-like segments."""
    from services.qwen.runner import _ensure_segment_window

    words = [
        {"start": 0.0, "end": 0.4, "word": "Hello"},
        {"start": 0.4, "end": 0.9, "word": "there."},
        {"start": 2.5, "end": 3.0, "word": "Another"},  # big gap -> new seg
        {"start": 3.0, "end": 3.4, "word": "sentence."},
    ]
    segments = _ensure_segment_window(words, clip_start=0.0, clip_end=10.0)
    assert len(segments) == 2
    assert "Hello" in segments[0]["text"]
    assert "Another" in segments[1]["text"]


def test_ensure_segment_window_passes_through_segments() -> None:
    """If the input is already segment-level, it's returned (filtered) as-is."""
    from services.qwen.runner import _ensure_segment_window

    segs = [
        {"start": 0.0, "end": 5.0, "text": "first sentence"},
        {"start": 5.0, "end": 12.0, "text": "second one"},
    ]
    out = _ensure_segment_window(segs, clip_start=0.0, clip_end=20.0)
    assert out == segs

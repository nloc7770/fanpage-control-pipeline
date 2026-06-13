"""Regression test: every Qwen prompt that drives reel openings must inject
the shared HOOK_FORMULA_VI block. If this test fails, viral hooks are not
being enforced and reels will revert to generic intros."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

PROMPTS_PATH = Path(__file__).resolve().parents[1] / "ai" / "prompts.py"


def _load_prompts():
    spec = importlib.util.spec_from_file_location("prompts", PROMPTS_PATH)
    assert spec and spec.loader, "could not load prompts module"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prompts = _load_prompts()


REQUIRED_PATTERN_LABELS = (
    "QUESTION HOOK",
    "CONTRARIAN",
    "PATTERN INTERRUPT",
    "STAKE",
    "NUMBERED LIST",
)

REQUIRED_BAD_OPENERS = (
    "Hôm nay",
    "Trong video này",
    "Xin chào",
)


def test_hook_formula_constant_lists_all_five_patterns():
    text = prompts.HOOK_FORMULA_VI
    for label in REQUIRED_PATTERN_LABELS:
        assert label in text, f"missing pattern label: {label}"
    for bad in REQUIRED_BAD_OPENERS:
        assert bad in text, f"missing rejected-opener example: {bad}"


@pytest.mark.parametrize(
    "name,args",
    [
        (
            "viral_clip_detection_messages",
            ([{"start": 0.0, "end": 5.0, "text": "hello"}], 60.0),
        ),
        (
            "edit_plan_messages",
            (
                {"start_time": 0.0, "end_time": 15.0, "main_hook": "x"},
                [{"start": 0.0, "end": 5.0, "text": "hello"}],
            ),
        ),
        (
            "narrative_rewrite_vi_messages",
            ([{"start": 0.0, "end": 5.0, "text": "hello"}], "a hook"),
        ),
    ],
)
def test_prompt_embeds_hook_formula(name, args):
    builder = getattr(prompts, name)
    messages = builder(*args)
    user_content = messages[-1]["content"]
    assert "HOOK FORMULA" in user_content, f"{name} missing HOOK FORMULA header"
    # At least 4 of the 5 pattern labels must be present in the embedded block.
    hits = sum(1 for lbl in REQUIRED_PATTERN_LABELS if lbl in user_content)
    assert hits >= 4, f"{name} only embeds {hits}/5 hook pattern labels"


def test_viral_clip_detection_requires_hook_first_segment():
    messages = prompts.viral_clip_detection_messages(
        [{"start": 0.0, "end": 5.0, "text": "hello"}], 60.0
    )
    content = messages[-1]["content"]
    assert "FIRST-SEGMENT SELECTION" in content
    assert "hook rewrite" in content.lower() or "hook segment" in content.lower()


def test_prompts_stay_under_token_budget():
    """Soft guard: keep each prompt under ~8k tokens (chars/4 heuristic)."""
    cases = [
        (
            "viral_clip_detection_messages",
            ([{"start": 0.0, "end": 5.0, "text": "x"}], 60.0),
        ),
        (
            "edit_plan_messages",
            (
                {"start_time": 0.0, "end_time": 15.0, "main_hook": "x"},
                [{"start": 0.0, "end": 5.0, "text": "x"}],
            ),
        ),
        (
            "narrative_rewrite_vi_messages",
            ([{"start": 0.0, "end": 5.0, "text": "x"}], "hook"),
        ),
    ]
    for name, args in cases:
        builder = getattr(prompts, name)
        messages = builder(*args)
        total_chars = sum(len(m["content"]) for m in messages)
        approx_tokens = total_chars // 4
        assert approx_tokens < 8000, f"{name} ~{approx_tokens} tokens exceeds 8k budget"

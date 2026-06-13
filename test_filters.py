"""Unit tests for discovery filters.

These cover the AI-generated content layer added on top of the existing
duration / livestream / privacy / music checks. Each test passes a
self-contained ``item`` dict + a stub page + stub settings into
``filters.passes`` and asserts on the ``(ok, reason)`` tuple shape that
callers (``services.discovery.youtube``) depend on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from services.discovery import filters


# ---------------------------------------------------------------------------
# Stubs — we deliberately don't import the real SQLAlchemy ``FacebookPage``
# model here; ``passes`` only touches ``page.blocked_keywords``.
# ---------------------------------------------------------------------------


@dataclass
class _StubPage:
    blocked_keywords: list[str] = field(default_factory=list)


@dataclass
class _StubSettings:
    YOUTUBE_MIN_DURATION_SECONDS: int = 180
    YOUTUBE_MAX_DURATION_SECONDS: int = 1800


def _item(title: str, description: str = "", duration: int = 600) -> dict[str, Any]:
    return {
        "title": title,
        "description": description,
        "duration_seconds": duration,
    }


def _passes(item: dict[str, Any]) -> tuple[bool, str | None]:
    return filters.passes(item, _StubPage(), _StubSettings())


# ---------------------------------------------------------------------------
# Baseline — must not regress real human content.
# ---------------------------------------------------------------------------


def test_real_human_content_passes() -> None:
    ok, reason = _passes(
        _item(
            "Real Fishing Trip Catch",
            "Took the boat out at dawn, full vlog of the day's catch.",
        )
    )
    assert ok is True
    assert reason is None


def test_chatgpt_tutorial_passes() -> None:
    """ChatGPT *as a topic* (tutorial / prompt engineering) is fine — it's
    a real human teaching about an AI tool, not AI-narrated content."""
    ok, reason = _passes(
        _item(
            "ChatGPT Tutorial: Prompt Engineering",
            "In this tutorial I walk through how to write better prompts.",
        )
    )
    assert ok is True, f"expected pass, got reason={reason}"


def test_plain_survival_story_passes() -> None:
    """A bare 'survival story' title is a soft signal only; without any
    AI corroboration in the description (and without the era + pipe
    channel-formula), it must not be rejected."""
    ok, reason = _passes(
        _item(
            "My Survival Story: Three Days Lost in Patagonia",
            "Hand-shot footage from our backcountry trip in Patagonia.",
        )
    )
    assert ok is True, f"expected pass, got reason={reason}"


def test_real_wilderness_survival_tips_passes() -> None:
    """Real human survival-tips content must not be caught by the new
    era + pipe channel-formula patterns."""
    ok, reason = _passes(
        _item(
            "Real Wilderness Survival Tips for Beginners",
            "Five tips I learned from a decade of backcountry trips.",
        )
    )
    assert ok is True, f"expected pass, got reason={reason}"


def test_catfish_survival_guide_with_pipe_passes() -> None:
    """Pipe separator alone is not enough — without an era word the
    title is treated as legitimate."""
    ok, reason = _passes(
        _item(
            "Catfish Survival Guide | Tips and Tricks",
            "How catfish survive in muddy rivers — angler's pocket guide.",
        )
    )
    assert ok is True, f"expected pass, got reason={reason}"


# ---------------------------------------------------------------------------
# Hard AI signals — single match in title or description is enough.
# ---------------------------------------------------------------------------


def test_ai_generated_in_title_rejected() -> None:
    ok, reason = _passes(_item("AI Generated Survival Story", ""))
    assert ok is False
    assert reason == "ai_generated"


def test_made_with_midjourney_rejected() -> None:
    ok, reason = _passes(
        _item(
            "Lost City of Atlantis",
            "Made with Midjourney and ElevenLabs voice.",
        )
    )
    assert ok is False
    assert reason == "ai_generated"


def test_ai_narrated_phrase_rejected() -> None:
    ok, reason = _passes(
        _item(
            "Ancient Rome: Day in the Life",
            "AI-narrated dramatization of a Roman legionary's day.",
        )
    )
    assert ok is False
    assert reason == "ai_generated"


def test_problem_video_title_alone_rejected() -> None:
    """The exact reported video — title alone is now a hard reject thanks
    to the 'Survival Was Never Meant to Be' tagline pattern, even with a
    generic description that has no AI keyword."""
    ok, reason = _passes(
        _item(
            "Prehistoric Survival Story | Survival Was Never Meant to Be This Hard",
            "Subscribe and like for more.",
        )
    )
    assert ok is False
    assert reason == "ai_generated"


def test_era_plus_pipe_formula_rejected() -> None:
    """Era word ('ancient') + storytelling noun ('tale') + pipe separator
    is the AI story-farm channel template. Empty description, still reject."""
    ok, reason = _passes(
        _item(
            "Ancient Survival Tale | He Was the Last of His Tribe",
            "",
        )
    )
    assert ok is False
    assert reason == "ai_generated"


# ---------------------------------------------------------------------------
# Soft AI signals — only fire when corroborated in the description.
# ---------------------------------------------------------------------------


def test_problem_video_with_ai_description_rejected() -> None:
    """The exact reported video: soft title shape + AI signal in desc."""
    ok, reason = _passes(
        _item(
            "Prehistoric Survival Story | Survival Was Never Meant to Be This Hard",
            "AI narrated immersive story. Generated with AI tools for your viewing pleasure.",
        )
    )
    assert ok is False
    assert reason == "ai_generated"


def test_dystopian_ai_story_rejected() -> None:
    ok, reason = _passes(
        _item(
            "Dystopian AI Story: The Last City",
            "Voiceover by ElevenLabs, animation by Runway.",
        )
    )
    assert ok is False
    assert reason == "ai_generated"


# ---------------------------------------------------------------------------
# Ordering — AI check fires before blocked_keyword fallback.
# ---------------------------------------------------------------------------


def test_ai_reason_takes_precedence_over_blocked_keyword() -> None:
    page = _StubPage(blocked_keywords=["survival"])
    ok, reason = filters.passes(
        _item(
            "AI Generated Survival Story",
            "narrated by AI.",
        ),
        page,
        _StubSettings(),
    )
    assert ok is False
    assert reason == "ai_generated"

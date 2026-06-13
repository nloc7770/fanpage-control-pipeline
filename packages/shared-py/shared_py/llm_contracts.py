"""JSON contracts the Qwen LLM must return.

These pydantic models double as the parser used by the qwen worker and as the
schema documented in `docs/llm-prompts.md`. Keep these in lockstep with the
prompt templates in that file -- if you change a field name here, change the
prompt too.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Base(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


# ---------------------------------------------------------------------------
# Viral clip detection (prompt: clip_detection)
# ---------------------------------------------------------------------------


class HighlightSegment(_Base):
    """One short highlight inside a recap clip (4-15s window in source time)."""

    start: float = Field(ge=0.0)
    end: float = Field(ge=0.0)
    reason: str = ""


class ClipDetectionItem(_Base):
    clip_index: int = Field(ge=0)
    start_time: float = Field(ge=0.0)
    end_time: float = Field(ge=0.0)
    duration: float = Field(ge=0.0)
    virality_score: float = Field(ge=0.0, le=10.0)
    main_hook: str
    emotional_peak: str
    retention_reason: str
    topics: list[str] = Field(default_factory=list)
    target_style: str
    highlight_segments: list[HighlightSegment] | None = None


class ClipDetectionResponse(_Base):
    clips: list[ClipDetectionItem]


# ---------------------------------------------------------------------------
# Edit planning (prompt: edit_plan)
# ---------------------------------------------------------------------------


class EditingStyle(_Base):
    aggressive_pacing: bool = False
    dynamic_subtitles: bool = False
    fast_zoom_cuts: bool = False
    visual_overlays: bool = False
    pattern_interrupts: bool = False
    cinematic_sound_design: bool = False


class SubtitleStyle(_Base):
    font: str | None = None
    size: int | None = None
    primary_color: str | None = None
    outline_color: str | None = None
    outline_width: int | None = None
    position: Literal["top", "middle", "bottom"] | None = None
    emphasis_color: str | None = None
    word_highlight: bool | None = None


class VisualEffect(_Base):
    type: str
    start: float = Field(ge=0.0)
    end: float = Field(ge=0.0)
    params: dict[str, Any] = Field(default_factory=dict)


class PatternInterrupt(_Base):
    at: float = Field(ge=0.0)
    kind: str
    params: dict[str, Any] = Field(default_factory=dict)


class CropKeyframe(_Base):
    t: float = Field(ge=0.0)
    x: float
    y: float
    w: float
    h: float


class CropPlan(_Base):
    mode: Literal["track_face", "center", "smart", "static"] = "smart"
    keyframes: list[CropKeyframe] = Field(default_factory=list)


class NarrativeSegment(_Base):
    """One Vietnamese narration slot, time-aligned to the source transcript.

    ``start`` and ``end`` are seconds **relative to the clip** (0 = clip start),
    NOT absolute source-video times. ``text_vi`` is the natural Vietnamese
    rewrite of the corresponding English transcript segment.
    """

    start: float = Field(ge=0.0)
    end: float = Field(ge=0.0)
    text_vi: str


class FbCaptionPackage(_Base):
    """Facebook-ready caption + hashtags + CTA for one clip.

    Stored inside ``EditPlan`` so the rendered clip ships with a paste-ready
    Vietnamese caption for the Facebook reup flow. All fields default to empty
    so historical edit plans (which lack this block) still validate.
    """

    caption: str = ""
    hashtags: list[str] = Field(default_factory=list)
    cta: str = ""
    niche: str = "other"


class EditPlan(_Base):
    @model_validator(mode="before")
    @classmethod
    def _accept_bare_segment_list(cls, v):
        # Qwen 2.5 occasionally returns just the narrative_segments array
        # as the top-level JSON value. Wrap it so the segment-rich payload
        # is preserved instead of failing fast with a model_type error.
        # Required scalar fields (clip_index/title/...) will still error,
        # which gives runner.py's recovery path a clean dict to enrich.
        if isinstance(v, list):
            return {"narrative_segments": v}
        return v

    clip_index: int = Field(ge=0)
    title: str
    hook: str
    summary: str
    viral_angle: str
    editing_style: EditingStyle
    # Legacy single-string field, kept for backward compat. New callers should
    # prefer ``narrative_segments`` -- the renderer will derive this string
    # from the segments if it's missing.
    narrative_script_vi: str | None = None
    # Time-aligned Vietnamese narration, one segment per source transcript
    # segment (seconds relative to clip start). Preferred over the single-
    # string field for the segment-aware TTS path.
    narrative_segments: list[NarrativeSegment] | None = None
    visual_effects: list[VisualEffect] = Field(default_factory=list)
    subtitle_style: SubtitleStyle = Field(default_factory=SubtitleStyle)
    pattern_interrupts: list[PatternInterrupt] = Field(default_factory=list)
    crop_plan: CropPlan = Field(default_factory=CropPlan)
    # Facebook-ready caption package for the reup flow. Optional for backward
    # compat with edit plans produced before this field was added.
    fb_caption_package: FbCaptionPackage | None = None


# ---------------------------------------------------------------------------
# Narrative rewrite VI (prompt: narrative_rewrite_vi)
# ---------------------------------------------------------------------------


class NarrativeRewriteVIResponse(_Base):
    clip_index: int
    narrative_script_vi: str


# ---------------------------------------------------------------------------
# Subtitle condensation (prompt: subtitle_condensation)
# ---------------------------------------------------------------------------


class CondensedSubtitleLine(_Base):
    start: float = Field(ge=0.0)
    end: float = Field(ge=0.0)
    text: str
    emphasis_words: list[str] = Field(default_factory=list)


class SubtitleCondensationResponse(_Base):
    clip_index: int
    lines: list[CondensedSubtitleLine]


# ---------------------------------------------------------------------------
# JSON repair (prompt: json_repair)
# ---------------------------------------------------------------------------


class JsonRepairResponse(_Base):
    """A wrapper that always parses; the `data` field holds the fixed payload."""

    data: dict[str, Any]

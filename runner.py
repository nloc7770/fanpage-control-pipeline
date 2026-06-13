"""Qwen orchestration: builds messages via ``packages/ai`` and validates output.

These helpers are the only place the worker tasks talk to the LLM. Mock mode
(``MOCK_LLM=1``) skips the HTTP roundtrip and returns deterministic fixtures
so the whole pipeline runs in seconds on a laptop.

Prompt-budget strategy
----------------------

The Qwen server enforces ``messages + max_tokens <= n_ctx`` (token count). We
size our character budget from two env vars:

* ``QWEN_CONTEXT_WINDOW`` -- the server's ``n_ctx`` (default 16384).
* ``QWEN_MAX_TOKENS``     -- the response reservation (default 8192).

We use a conservative 4-chars-per-token approximation and keep a 512-token
safety margin for the message tokenizer's overhead. ``detect_clips`` and
``plan_edit`` override ``max_tokens`` per call so they can spend more of the
window on the prompt and less on the response.

For ``detect_clips`` we always try a single-pass call first; only if the
prompt overflows do we fall back to overlapping time-window chunking. Results
from chunks are merged and de-duplicated by IoU overlap.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from ai.prompts import (
    edit_plan_messages,
    viral_clip_detection_messages,
)
from ai.qwen_client import QwenClient, QwenContextOverflowError
from shared_py.llm_contracts import (
    ClipDetectionItem,
    ClipDetectionResponse,
    CropKeyframe,
    CropPlan,
    EditingStyle,
    EditPlan,
    HighlightSegment,
    NarrativeSegment,
    SubtitleStyle,
)


# Clip duration constraints (single continuous moment, TikTok sweet spot).
CLIP_MIN_S = 15.0
CLIP_MAX_S = 60.0
# Legacy highlight constraints kept for backward compat with highlight_segments
# field (now optional single-entry).
HIGHLIGHT_MIN_S = 4.0
HIGHLIGHT_MAX_S = 60.0
HIGHLIGHT_FALLBACK_S = 60.0


# ---------------------------------------------------------------------------
# Prompt-budget constants
# ---------------------------------------------------------------------------

# max_tokens defaults per call kind. detect_clips returns one JSON object with
# 5-15 small clip entries -- but each clip now carries 4-10 highlight_segments
# (start/end/reason), so the response can grow to ~6 KB for a busy 10-clip
# response. 6000 tokens leaves comfortable headroom while still fitting inside
# n_ctx=16384. plan_edit returns a richer EditPlan with crop keyframes /
# effects, but still fits in 2k tokens comfortably.
DETECT_CLIPS_MAX_TOKENS = 6000
PLAN_EDIT_MAX_TOKENS = 2048

# Safety margin (in tokens) to absorb tokenizer overhead vs. our chars/4
# approximation, plus the system prompt's contribution.
_SAFETY_TOKENS = 512

# ~4 chars per token is a conservative English-and-mixed-content approximation
# for Qwen's BPE tokenizer.
_CHARS_PER_TOKEN = 4


def _ctx_window_tokens() -> int:
    """Server's ``n_ctx`` (token count). Configurable via env."""
    try:
        return int(os.environ.get("QWEN_CONTEXT_WINDOW", "16384"))
    except ValueError:
        return 16384


def _prompt_char_budget(*, response_tokens: int) -> int:
    """Approximate prompt-size ceiling in characters.

    Server enforces ``message_tokens + max_tokens <= n_ctx``. We translate the
    remaining token budget (minus a safety margin) to characters via the
    rough ``_CHARS_PER_TOKEN`` ratio.
    """
    prompt_tokens = max(512, _ctx_window_tokens() - response_tokens - _SAFETY_TOKENS)
    return prompt_tokens * _CHARS_PER_TOKEN


# Module-level alias kept for backwards-compatible imports. Callers should
# prefer ``_prompt_char_budget(response_tokens=...)`` which respects the
# per-call max_tokens override. This default assumes the largest reservation
# we use (PLAN_EDIT_MAX_TOKENS) so it's safe as a generic ceiling.
PROMPT_CHAR_BUDGET = _prompt_char_budget(response_tokens=PLAN_EDIT_MAX_TOKENS)

# Default window/stride for the chunked fallback path. With n_ctx=16384 a
# single-pass prompt can comfortably fit ~22 minutes of dense transcript, so
# chunking is rarely needed. When it is, we use 5-minute windows with 30s
# overlap -- large enough that fragmentation is minimal but small enough to
# always stay under the budget.
DEFAULT_WINDOW_S = 300.0  # 5 minutes
DEFAULT_STRIDE_S = 270.0  # 4.5 minutes (-> 30s overlap)

# IoU threshold for merging duplicate clips across windows.
MERGE_IOU_THRESHOLD = 0.5


# ---------------------------------------------------------------------------
# AnalysisResult is the dataclass form of the DB row -- distinct from the
# pydantic LLM contracts (we don't have a dedicated prompt for "analysis";
# we synthesise it from the detection response + signals).
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AnalysisResult:
    emotional_peaks: list[dict[str, Any]] = field(default_factory=list)
    viral_moments: list[dict[str, Any]] = field(default_factory=list)
    topic_shifts: list[dict[str, Any]] = field(default_factory=list)
    retention_signals: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""


# ---------------------------------------------------------------------------
# Quality validation gate
# ---------------------------------------------------------------------------

class LLMQualityError(RuntimeError):
    """Raised when LLM clip detection output fails quality checks.

    The message describes which gate failed so callers can decide whether to
    retry with a stronger prompt or surface the error upstream.
    """


def _validate_clip_quality(
    response: ClipDetectionResponse,
    *,
    duration: float,
) -> None:
    """Raise :class:`LLMQualityError` (LLM output rejected) if the response
    looks like a low-effort placeholder rather than genuine highlight detection.

    Five gates (any single failure raises):
    1. virality_score >= 7.0 for at least 80% of clips → no lazy defaults
    2. main_hook.strip() >= 10 chars AND matches hook formula for >= 80% of clips
    3. retention_reason must cite one of the 6 platform metrics for >= 80% of clips
    4. duration must be 15.0-60.0 for each clip
    5. For sources longer than 5 minutes: span of start_times must exceed 5% of
       source duration → sliding-window artifact (0, 20, 40, 60, 80 …)
    """
    clips = response.clips
    if not clips:
        return  # no clips → let downstream raise

    n = len(clips)

    # Gate 1: virality_score quality (80% must be >= 7.0)
    high_score = sum(1 for c in clips if c.virality_score >= 7.0)
    if high_score / n < 0.8:
        raise LLMQualityError(
            f"LLM output rejected: {high_score}/{n} clips have virality_score >= 7.0 "
            f"(expected >= 80%). Model returned low-quality or default scores."
        )

    # Gate 2: main_hook quality (10+ chars AND matches hook formula)
    _HOOK_FORMULAS = ("QUESTION", "NUMBER", "SURPRISE", "DIRECT ADDRESS", "PATTERN INTERRUPT")
    def _hook_valid(hook: str) -> bool:
        h = hook.strip()
        if len(h) < 10:
            return False
        # Hook must either start with a formula label (e.g. "NUMBER: ...") or
        # be descriptive enough (10+ chars is the minimum bar).
        # We check for formula prefix OR sufficient length+content.
        h_upper = h.upper()
        has_formula = any(h_upper.startswith(f) for f in _HOOK_FORMULAS)
        # Accept hooks that are 10+ chars even without explicit formula prefix,
        # since the LLM may embed the hook type implicitly.
        return has_formula or len(h) >= 10

    hooked = sum(1 for c in clips if _hook_valid(c.main_hook))
    if hooked / n < 0.8:
        raise LLMQualityError(
            f"LLM output rejected: only {hooked}/{n} clips have a valid main_hook "
            f"(>= 10 chars, expected >= 80%). Model did not generate proper hooks."
        )

    # Gate 3: retention_reason must cite platform metrics
    _RETENTION_METRICS = (
        "curiosity_gap", "curiosity gap",
        "emotional_shock", "emotional shock",
        "visual_spectacle", "visual spectacle",
        "humor",
        "fomo",
        "relatability",
    )
    def _retention_valid(reason: str) -> bool:
        r = reason.strip().lower()
        return any(metric in r for metric in _RETENTION_METRICS)

    valid_retention = sum(1 for c in clips if _retention_valid(c.retention_reason))
    if valid_retention / n < 0.8:
        raise LLMQualityError(
            f"LLM output rejected: only {valid_retention}/{n} clips cite a valid "
            f"retention metric (curiosity_gap, emotional_shock, visual_spectacle, "
            f"humor, fomo, relatability). Expected >= 80%."
        )

    # Gate 4: duration must be 15-60s for each clip
    bad_duration = [
        c for c in clips
        if c.duration < 15.0 or c.duration > 60.0
    ]
    if bad_duration:
        raise LLMQualityError(
            f"LLM output rejected: {len(bad_duration)}/{n} clips have duration "
            f"outside 15-60s range. Clips must be single continuous moments "
            f"in the TikTok sweet spot."
        )

    # Gate 5: sliding-window artifact (only for sources > 5 minutes)
    if duration > 300.0 and n >= 3:
        starts = [c.start_time for c in clips]
        span = max(starts) - min(starts)
        threshold = 0.05 * duration
        if span <= threshold:
            raise LLMQualityError(
                f"LLM output rejected: start_time span={span:.1f}s <= {threshold:.1f}s "
                f"(5% of {duration:.0f}s). Clips look like a sliding-window artifact, "
                f"not genuine highlight detection."
            )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_clips(
    transcript_segments: list[dict[str, Any]],
    signals: dict[str, Any],
    duration: float,
    *,
    target_clip_count: int = 5,
    client: QwenClient | None = None,
) -> ClipDetectionResponse:
    """Return viral clip candidates.

    For long transcripts that would exceed the Qwen server's context window,
    the transcript is split into overlapping time windows and each window is
    queried separately. Results are merged + de-duplicated and re-indexed.
    """
    if _is_mock():
        return _mock_clip_detection(duration=duration, target=target_clip_count)

    owned_client = client is None
    qwen = client or QwenClient()
    detect_budget = _prompt_char_budget(response_tokens=DETECT_CLIPS_MAX_TOKENS)
    try:
        # Primary path: single call. With n_ctx=16384 a ~22-min transcript
        # serialised as compact {start,end,text} segments fits comfortably.
        single_msgs = viral_clip_detection_messages(
            transcript_segments=transcript_segments,
            video_duration=duration,
            signals=signals,
            target_clip_count=target_clip_count,
        )
        single_chars = _messages_chars(single_msgs)
        if single_chars <= detect_budget:
            logger.info(
                "detect_clips: single-pass (prompt_chars={} budget={} segments={})",
                single_chars,
                detect_budget,
                len(transcript_segments or []),
            )
            try:
                resp = qwen.chat_json(
                    single_msgs,
                    ClipDetectionResponse,
                    max_tokens=DETECT_CLIPS_MAX_TOKENS,
                )
                try:
                    _validate_clip_quality(resp, duration=duration)
                except LLMQualityError as qe:
                    logger.warning(
                        "detect_clips: single-pass quality gate FAILED ({}). "
                        "Retrying once with stricter prompt.",
                        qe,
                    )
                    retry_msgs = viral_clip_detection_messages(
                        transcript_segments=transcript_segments,
                        video_duration=duration,
                        signals=signals,
                        target_clip_count=target_clip_count,
                        rejection_reason=str(qe),
                    )
                    resp = qwen.chat_json(
                        retry_msgs,
                        ClipDetectionResponse,
                        max_tokens=DETECT_CLIPS_MAX_TOKENS,
                    )
                    try:
                        _validate_clip_quality(resp, duration=duration)
                    except LLMQualityError as qe2:
                        logger.warning(
                            "detect_clips: retry also failed quality gate ({}). "
                            "Raising — LLM output rejected, not fabricating clips.",
                            qe2,
                        )
                        raise
                return _normalise_clip_highlights(resp, duration=duration)
            except QwenContextOverflowError as exc:
                logger.warning(
                    "detect_clips: single-pass overflow ({}), falling back to chunking",
                    exc,
                )
                # Fall through to chunked path.
        else:
            logger.info(
                "detect_clips: prompt_chars={} exceeds budget={}, using chunked fallback",
                single_chars,
                detect_budget,
            )

        # Chunked fallback path.
        windows = chunk_transcript_by_time(
            transcript_segments or [],
            duration=duration,
            window_s=DEFAULT_WINDOW_S,
            stride_s=DEFAULT_STRIDE_S,
        )
        logger.info(
            "detect_clips: chunked into {} windows (window={}s stride={}s "
            "prompt_chars_single={} segments={})",
            len(windows),
            DEFAULT_WINDOW_S,
            DEFAULT_STRIDE_S,
            single_chars,
            len(transcript_segments or []),
        )

        # Distribute target_clip_count proportionally across windows but with
        # a floor of 2 per window so each window contributes options.
        per_window_target = max(2, (target_clip_count + len(windows) - 1) // len(windows) + 1)

        all_items: list[ClipDetectionItem] = []
        for idx, (w_start, w_end, w_segments) in enumerate(windows):
            if not w_segments:
                continue
            w_signals = _slice_signals(signals, w_start, w_end)
            w_msgs = viral_clip_detection_messages(
                transcript_segments=w_segments,
                video_duration=duration,
                signals=w_signals,
                target_clip_count=per_window_target,
            )
            w_chars = _messages_chars(w_msgs)
            if w_chars > detect_budget:
                # Defensive trim: if even a window blows the budget, drop the
                # signals (diarization / visual summary) and try again.
                logger.warning(
                    "detect_clips: window {} ({:.0f}-{:.0f}) prompt_chars={} "
                    "exceeds budget {}, trimming signals",
                    idx, w_start, w_end, w_chars, detect_budget,
                )
                w_msgs = viral_clip_detection_messages(
                    transcript_segments=w_segments,
                    video_duration=duration,
                    signals={},
                    target_clip_count=per_window_target,
                )
            logger.info(
                "detect_clips: window {}/{} t=[{:.0f},{:.0f}] segments={} "
                "prompt_chars={}",
                idx + 1, len(windows), w_start, w_end,
                len(w_segments), _messages_chars(w_msgs),
            )
            try:
                resp = qwen.chat_json(
                    w_msgs,
                    ClipDetectionResponse,
                    max_tokens=DETECT_CLIPS_MAX_TOKENS,
                )
            except QwenContextOverflowError as exc:
                logger.error(
                    "detect_clips: window {} STILL overflows after trim ({}), skipping",
                    idx, exc,
                )
                continue
            except Exception as exc:
                logger.error(
                    "detect_clips: window {} failed ({}): {}",
                    idx, type(exc).__name__, exc,
                )
                continue
            # Clamp items to the window's time bounds (LLM sometimes hallucinates
            # outside) and into [0, duration].
            for item in resp.clips:
                if item.end_time <= item.start_time:
                    continue
                if item.end_time < w_start - 1.0 or item.start_time > w_end + 1.0:
                    # Drop items that clearly drifted outside this window.
                    continue
                all_items.append(item)

        merged = _merge_and_dedup_clips(all_items, duration=duration)
        # Cap to target_clip_count, prefer higher virality_score.
        merged.sort(key=lambda c: c.virality_score, reverse=True)
        merged = merged[: max(1, target_clip_count)]
        # Re-index in chronological order.
        merged.sort(key=lambda c: c.start_time)
        for i, item in enumerate(merged):
            item.clip_index = i
        logger.info(
            "detect_clips: merged {} unique clips from {} window candidates",
            len(merged), len(all_items),
        )
        if not merged:
            logger.warning(
                "detect_clips: no clips survived chunked detection, returning mock fallback"
            )
            return _mock_clip_detection(duration=duration, target=target_clip_count)
        return _normalise_clip_highlights(
            ClipDetectionResponse(clips=merged), duration=duration
        )
    finally:
        if owned_client:
            qwen.close()


def plan_edit(
    clip: dict[str, Any] | ClipDetectionItem,
    transcript_window: list[dict[str, Any]],
    yolo_hints: dict[str, Any] | None = None,
    *,
    client: QwenClient | None = None,
) -> EditPlan:
    """Return an :class:`EditPlan` for one clip.

    ``transcript_window`` may be either word-level (``{start, end, word}``) or
    segment-level (``{start, end, text}``) -- when given words, we group them
    into ~sentence-sized segments before handing to the prompt so the LLM can
    produce one Vietnamese narration slot per English speaking burst.
    """
    clip_dict = (
        clip.model_dump() if isinstance(clip, ClipDetectionItem) else dict(clip)
    )
    if _is_mock():
        return _mock_edit_plan(clip_dict)

    plan_budget = _prompt_char_budget(response_tokens=PLAN_EDIT_MAX_TOKENS)

    # Defensive: filter transcript to ±10s around the clip and budget chars.
    # We reserve half the prompt budget for the transcript window so there's
    # room for the clip object, yolo hints, schema, and system text.
    clip_start = float(clip_dict.get("start_time", 0.0))
    clip_end = float(clip_dict.get("end_time", 0.0))
    clip_duration = max(0.0, clip_end - clip_start)

    # Normalise transcript_window into segment-level form before prompting.
    # The new prompt aligns one Vietnamese narrative segment per input
    # transcript segment, so we want the input to be sentence-ish chunks (not
    # individual words). The qwen_tasks loader still returns word-level data
    # so we group it here.
    segment_window = _ensure_segment_window(
        transcript_window or [],
        clip_start=clip_start,
        clip_end=clip_end,
    )
    trimmed_window = _trim_transcript_window(
        segment_window,
        start=clip_start - 10.0,
        end=clip_end + 10.0,
        budget_chars=plan_budget // 2,
    )

    messages = edit_plan_messages(
        clip=clip_dict,
        transcript_window=trimmed_window,
        yolo_hints=yolo_hints,
    )
    chars = _messages_chars(messages)
    if chars > plan_budget:
        logger.warning(
            "plan_edit: prompt_chars={} > budget {}, dropping yolo_hints",
            chars, plan_budget,
        )
        messages = edit_plan_messages(
            clip=clip_dict, transcript_window=trimmed_window, yolo_hints=None,
        )

    owned_client = client is None
    qwen = client or QwenClient()
    try:
        try:
            plan = qwen.chat_json(messages, EditPlan, max_tokens=PLAN_EDIT_MAX_TOKENS)
        except Exception as exc:
            # Defensive recovery: if Qwen returned a bare ``narrative_segments``
            # array (we've seen this happen when the model latches onto the
            # segments example), fall back to a raw chat() + manual wrap.
            # Prefer the raw text captured inside the ValidationError so we
            # don't waste another LLM round-trip.
            text = getattr(exc, "last_raw_text", None)
            if not text:
                text = qwen.chat(
                    messages,
                    response_format="json",
                    temperature=0.2,
                    max_tokens=PLAN_EDIT_MAX_TOKENS,
                )
            import json as _json
            from ai.json_repair import try_parse_json as _try_parse

            parsed = _try_parse(text)
            if parsed is None:
                try:
                    parsed = _json.loads(text)
                except Exception:
                    raise exc
            if isinstance(parsed, list):
                logger.warning(
                    "plan_edit: Qwen returned a bare list ({} items), "
                    "wrapping as narrative_segments only",
                    len(parsed),
                )
                wrapped = _wrap_bare_segments_list(parsed, clip_dict)
                try:
                    plan = EditPlan.model_validate(wrapped)
                except Exception as wrap_exc:
                    logger.error(
                        "plan_edit: wrap validation also failed: {}", wrap_exc
                    )
                    raise exc
            elif isinstance(parsed, dict):
                # If Qwen returned a dict but only `narrative_segments` (no other
                # required fields), wrap it. Otherwise try to validate verbatim.
                try:
                    plan = EditPlan.model_validate(parsed)
                except Exception:
                    if "narrative_segments" in parsed and isinstance(
                        parsed["narrative_segments"], list
                    ):
                        logger.warning(
                            "plan_edit: Qwen returned partial dict, wrapping with clip metadata"
                        )
                        wrapped = _wrap_bare_segments_list(
                            parsed["narrative_segments"], clip_dict
                        )
                        # Merge any extra fields Qwen DID return on top of the wrap.
                        for k, v in parsed.items():
                            if k not in wrapped or wrapped[k] in (None, "", [], {}):
                                wrapped[k] = v
                        plan = EditPlan.model_validate(wrapped)
                    else:
                        raise exc
            else:
                raise exc
    finally:
        if owned_client:
            qwen.close()

    # Backward-compat + safety: normalise narrative_segments. If Qwen returned
    # only the legacy ``narrative_script_vi``, synthesise segments by chunking
    # it evenly across the input transcript segments.
    plan = _normalise_narrative_segments(
        plan,
        input_segments=trimmed_window,
        clip_start=clip_start,
        clip_duration=clip_duration,
    )
    _warn_literal_translation(plan)
    return plan


def _warn_literal_translation(plan: "EditPlan") -> None:  # noqa: F821
    """Log a WARNING when obvious literal-translation artifacts are detected.

    This is a non-blocking quality signal for developers -- the plan is returned
    as-is even if issues are found.

    Traps checked (lower-cased):
    - "cá chơi game"  : 'game fish' mistranslated as video-game fish
    - "chơi game"     : generic game-fish false cognate
    - "cậu bé trắng"  : 'White boy' (fishing nickname) taken literally
    - "cậu bé da trắng": same, with skin-colour gloss
    - "tài khoản"     : 'accounts' (stories) rendered as bank/user accounts
    - "được ghi chép tốt": 'well-documented' stiff calque
    """
    _TRAPS: list[tuple[str, str]] = [
        ("cá chơi game", "'game fish' -> 'cá chơi game' (should be 'cá săn câu' / 'cá thể thao')"),
        ("chơi game", "possible 'game fish' false-cognate: 'chơi game' in fishing context"),
        ("cậu bé trắng", "'White boy' nickname -> literal 'cậu bé trắng'"),
        ("cậu bé da trắng", "'White boy' nickname -> literal 'cậu bé da trắng'"),
        ("tài khoản", "'accounts' (stories) -> 'tài khoản' (financial/user account)"),
        ("được ghi chép tốt", "'well-documented' stiff calque"),
    ]
    # Collect all Vietnamese text fields to check.
    vi_texts: list[str] = []
    if plan.narrative_script_vi:
        vi_texts.append(plan.narrative_script_vi)
    for seg in plan.narrative_segments or []:
        if seg.text_vi:
            vi_texts.append(seg.text_vi)
    combined = " ".join(vi_texts).lower()
    for pattern, description in _TRAPS:
        if pattern in combined:
            logger.warning(
                "plan_edit: literal-translation artifact detected -- %s", description
            )


# ---------------------------------------------------------------------------
# Narrative-segment helpers (used by plan_edit)
# ---------------------------------------------------------------------------


def _wrap_bare_segments_list(
    parsed_list: list[Any], clip_dict: dict[str, Any]
) -> dict[str, Any]:
    """Recover an EditPlan when Qwen returns only a bare narrative_segments list.

    Builds a minimal valid plan whose narrative segments are the model's list
    and whose other fields are sensible defaults derived from the clip.
    """
    idx = int(clip_dict.get("clip_index", 0))
    title = (clip_dict.get("main_hook") or f"Clip #{idx + 1}").strip()
    # Build the text_vi join for the legacy field.
    joined = " ".join(
        str(s.get("text_vi") or "").strip()
        for s in parsed_list
        if isinstance(s, dict)
    ).strip()
    return {
        "clip_index": idx,
        "title": title,
        "hook": title,
        "summary": title,
        "viral_angle": "curiosity",
        "editing_style": {
            "aggressive_pacing": True,
            "dynamic_subtitles": True,
            "fast_zoom_cuts": False,
            "visual_overlays": False,
            "pattern_interrupts": False,
            "cinematic_sound_design": False,
        },
        "narrative_script_vi": joined,
        "narrative_segments": parsed_list,
        "visual_effects": [],
        "subtitle_style": {},
        "pattern_interrupts": [],
        "crop_plan": {"mode": "smart", "keyframes": []},
    }


def _ensure_segment_window(
    window: list[dict[str, Any]],
    *,
    clip_start: float,
    clip_end: float,
) -> list[dict[str, Any]]:
    """Return a segment-level transcript ([{start,end,text}, ...]).

    If the input already looks segment-level (items carry ``text``), it is
    passed through (only filtered to the clip window). Otherwise we treat the
    items as word-level (``{start, end, word}``) and group them into ~sentence
    chunks split on long pauses or sentence-ending punctuation. All times are
    in the SOURCE timeline (same convention as the input).
    """
    if not window:
        return []
    # Heuristic: if the first item has a non-empty 'text' field, treat as segments.
    first = window[0]
    has_text = isinstance(first.get("text"), str) and first.get("text", "").strip()
    has_word = isinstance(first.get("word"), str) and first.get("word", "").strip()
    if has_text and not has_word:
        # Already segment-level; just filter to the clip window.
        out: list[dict[str, Any]] = []
        for s in window:
            try:
                s_start = float(s.get("start", 0.0))
                s_end = float(s.get("end", s_start))
            except (TypeError, ValueError):
                continue
            if s_end <= clip_start or s_start >= clip_end:
                continue
            text = (s.get("text") or "").strip()
            if not text:
                continue
            out.append({"start": s_start, "end": s_end, "text": text})
        return out

    # Word-level path: group into segments by gap + sentence punctuation.
    return _group_words_into_segments(window, clip_start=clip_start, clip_end=clip_end)


def _group_words_into_segments(
    words: list[dict[str, Any]],
    *,
    clip_start: float,
    clip_end: float,
    pause_gap_s: float = 0.6,
    max_segment_s: float = 8.0,
    max_words: int = 30,
) -> list[dict[str, Any]]:
    """Group word-level entries into sentence-ish segments.

    A new segment starts when:
      * the gap between consecutive words exceeds ``pause_gap_s``,
      * the previous word ended with sentence-ending punctuation,
      * the current segment exceeds ``max_segment_s`` or ``max_words``.
    """
    filtered: list[dict[str, Any]] = []
    for w in words:
        try:
            ws = float(w.get("start", 0.0))
            we = float(w.get("end", ws + 0.1))
        except (TypeError, ValueError):
            continue
        if we <= clip_start or ws >= clip_end:
            continue
        token = str(w.get("word") or w.get("text") or "").strip()
        if not token:
            continue
        filtered.append({"start": ws, "end": we, "word": token})

    if not filtered:
        return []

    segments: list[dict[str, Any]] = []
    cur_words: list[dict[str, Any]] = []
    cur_start = filtered[0]["start"]
    prev_end = filtered[0]["start"]

    def _flush() -> None:
        if not cur_words:
            return
        text = " ".join(w["word"] for w in cur_words).strip()
        if not text:
            return
        segments.append(
            {
                "start": cur_words[0]["start"],
                "end": cur_words[-1]["end"],
                "text": text,
            }
        )

    for w in filtered:
        gap = w["start"] - prev_end
        seg_dur = w["end"] - cur_start if cur_words else 0.0
        prev_token = cur_words[-1]["word"] if cur_words else ""
        ends_sentence = prev_token.endswith((".", "!", "?", "…"))
        boundary = (
            (cur_words and gap > pause_gap_s)
            or (cur_words and ends_sentence)
            or seg_dur > max_segment_s
            or len(cur_words) >= max_words
        )
        if boundary:
            _flush()
            cur_words = [w]
            cur_start = w["start"]
        else:
            if not cur_words:
                cur_start = w["start"]
            cur_words.append(w)
        prev_end = w["end"]
    _flush()
    return segments


def _normalise_narrative_segments(
    plan: EditPlan,
    *,
    input_segments: list[dict[str, Any]],
    clip_start: float,
    clip_duration: float,
) -> EditPlan:
    """Validate / clamp / backfill ``plan.narrative_segments``.

    * If empty but ``narrative_script_vi`` is set, split it evenly across the
      input transcript segments (best-effort fallback).
    * Validate ``0 <= start < end <= clip_duration``; clamp out-of-range.
    * Sort by start.
    * Keep ``narrative_script_vi`` consistent: join all ``text_vi`` with a
      single space if the legacy field is missing.
    """
    segs_in = list(plan.narrative_segments or [])

    if not segs_in and (plan.narrative_script_vi or "").strip():
        # Fallback: derive segments by splitting the single-string narrative
        # evenly across the input transcript segments (relative to clip).
        rel_segments: list[tuple[float, float]] = []
        for s in input_segments:
            try:
                rel_s = max(0.0, float(s.get("start", 0.0)) - clip_start)
                rel_e = max(rel_s, float(s.get("end", rel_s)) - clip_start)
            except (TypeError, ValueError):
                continue
            if clip_duration > 0:
                rel_s = min(rel_s, clip_duration)
                rel_e = min(rel_e, clip_duration)
            if rel_e > rel_s:
                rel_segments.append((rel_s, rel_e))
        if not rel_segments and clip_duration > 0:
            rel_segments = [(0.0, clip_duration)]

        text = plan.narrative_script_vi.strip()
        # Naive split by sentence punctuation first; fall back to char-even.
        import re as _re

        parts = [p.strip() for p in _re.split(r"(?<=[.!?…])\s+", text) if p.strip()]
        if len(parts) < len(rel_segments):
            # Pad by splitting longest parts on commas / spaces until we match.
            while parts and len(parts) < len(rel_segments):
                # Split the longest piece on a comma boundary first.
                idx = max(range(len(parts)), key=lambda i: len(parts[i]))
                piece = parts[idx]
                if "," in piece:
                    left, right = piece.split(",", 1)
                    parts[idx : idx + 1] = [left.strip(), right.strip()]
                else:
                    words = piece.split()
                    if len(words) < 2:
                        break
                    mid = len(words) // 2
                    parts[idx : idx + 1] = [
                        " ".join(words[:mid]),
                        " ".join(words[mid:]),
                    ]
        if len(parts) > len(rel_segments):
            # Merge tails so we end up with one part per rel_segment.
            head = parts[: max(1, len(rel_segments) - 1)]
            tail = " ".join(parts[len(head) :])
            parts = head + [tail]
        if not rel_segments:
            # No transcript at all -- emit a single segment spanning the clip.
            segs_in = [
                NarrativeSegment(
                    start=0.0,
                    end=max(0.1, clip_duration),
                    text_vi=text,
                )
            ]
        else:
            segs_in = []
            for (rs, re_), txt in zip(rel_segments, parts):
                segs_in.append(
                    NarrativeSegment(start=rs, end=re_, text_vi=txt or text)
                )

    # Validate / clamp.
    cleaned: list[NarrativeSegment] = []
    for s in segs_in:
        s_start = max(0.0, float(s.start))
        s_end = float(s.end)
        if clip_duration > 0:
            s_end = min(s_end, clip_duration)
            s_start = min(s_start, clip_duration)
        if s_end <= s_start:
            # Last-ditch: give it a 0.2s slot or drop.
            s_end = min(s_start + 0.2, clip_duration if clip_duration > 0 else s_start + 0.2)
            if s_end <= s_start:
                continue
        text_vi = (s.text_vi or "").strip()
        if not text_vi:
            continue
        cleaned.append(NarrativeSegment(start=s_start, end=s_end, text_vi=text_vi))

    cleaned.sort(key=lambda s: s.start)

    # Sync the legacy single-string field.
    if cleaned:
        joined = " ".join(s.text_vi for s in cleaned).strip()
        if not (plan.narrative_script_vi or "").strip():
            plan.narrative_script_vi = joined
        plan.narrative_segments = cleaned
    else:
        plan.narrative_segments = None

    return plan


def analyze_content(
    transcript: list[dict[str, Any]],
    speakers: list[dict[str, Any]],
    *,
    clip_detection: ClipDetectionResponse | None = None,
) -> AnalysisResult:
    """Build the analysis_results payload from upstream signals.

    Phase 2 doesn't use a dedicated LLM call for the analysis -- the clip
    detection response already contains the high-signal events. We simply
    project them into the shape the DB row expects. This keeps the prompt
    budget bounded.
    """
    detection = clip_detection or _mock_clip_detection(duration=600.0, target=3)
    viral_moments = [
        {
            "clip_index": item.clip_index,
            "start": item.start_time,
            "end": item.end_time,
            "score": item.virality_score,
            "hook": item.main_hook,
        }
        for item in detection.clips
    ]
    emotional_peaks = [
        {
            "t": (item.start_time + item.end_time) / 2,
            "peak": item.emotional_peak,
            "score": item.virality_score,
        }
        for item in detection.clips
    ]
    retention_signals = [
        {
            "clip_index": item.clip_index,
            "reason": item.retention_reason,
        }
        for item in detection.clips
    ]
    topic_shifts = [
        {
            "t": item.start_time,
            "topics": item.topics,
        }
        for item in detection.clips
    ]
    summary = (
        f"Found {len(detection.clips)} candidate clips covering "
        f"{sum(c.duration for c in detection.clips):.1f}s of content "
        f"across {len(speakers) or 1} speaker(s)."
    )
    logger.info("qwen.analyze_content: summary={!r}", summary)
    _ = transcript  # currently unused -- kept for future signal mining
    return AnalysisResult(
        emotional_peaks=emotional_peaks,
        viral_moments=viral_moments,
        topic_shifts=topic_shifts,
        retention_signals=retention_signals,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# Chunking helpers
# ---------------------------------------------------------------------------


def _messages_chars(messages: list[dict[str, str]]) -> int:
    """Approximate prompt size in characters (4 chars ~= 1 token)."""
    return sum(len(m.get("content", "")) for m in messages)


def chunk_transcript_by_time(
    segments: list[dict[str, Any]],
    *,
    duration: float,
    window_s: float = DEFAULT_WINDOW_S,
    stride_s: float = DEFAULT_STRIDE_S,
) -> list[tuple[float, float, list[dict[str, Any]]]]:
    """Split ``segments`` into overlapping time windows.

    Returns a list of ``(window_start, window_end, segments_in_window)``
    tuples. ``stride_s`` is how far each window's start advances; the overlap
    is ``window_s - stride_s``. Segments are included in a window when they
    intersect the window's time range.

    The last window's end is clamped to ``duration`` (or the last segment's
    end if duration is 0). At least one window is always returned (it may be
    empty if ``segments`` is empty).
    """
    if window_s <= 0:
        raise ValueError("window_s must be > 0")
    if stride_s <= 0:
        raise ValueError("stride_s must be > 0")

    # Establish an effective duration. If duration is missing/0, use the last
    # segment's end.
    effective_duration = float(duration or 0.0)
    if segments:
        last_end = max(float(s.get("end", 0.0)) for s in segments)
        effective_duration = max(effective_duration, last_end)
    if effective_duration <= 0:
        return [(0.0, 0.0, list(segments))]

    out: list[tuple[float, float, list[dict[str, Any]]]] = []
    w_start = 0.0
    while w_start < effective_duration:
        w_end = min(w_start + window_s, effective_duration)
        window_segs = [
            s
            for s in segments
            if float(s.get("end", 0.0)) > w_start
            and float(s.get("start", 0.0)) < w_end
        ]
        out.append((w_start, w_end, window_segs))
        if w_end >= effective_duration:
            break
        w_start += stride_s
    return out


def _slice_signals(
    signals: dict[str, Any] | None, start: float, end: float
) -> dict[str, Any]:
    """Return a copy of ``signals`` with diarization / visual_summary sliced to ``[start, end]``."""
    if not signals:
        return {}
    out: dict[str, Any] = {}
    diar = signals.get("diarization") or []
    if isinstance(diar, list):
        sliced_diar: list[dict[str, Any]] = []
        for entry in diar:
            timeline = entry.get("timeline") or []
            kept = [
                t for t in timeline
                if float(t.get("end", 0.0)) > start
                and float(t.get("start", 0.0)) < end
            ]
            if kept:
                sliced_diar.append({
                    "speaker_id": entry.get("speaker_id"),
                    "timeline": kept,
                })
        if sliced_diar:
            out["diarization"] = sliced_diar
    vs = signals.get("visual_summary")
    if isinstance(vs, dict):
        # Keep only the compact summary fields (face_present_pct, duration_s).
        # The full focal_track is too large per-window; plan_edit gets it.
        compact: dict[str, Any] = {}
        for k in ("face_present_pct", "duration_s"):
            if k in vs:
                compact[k] = vs[k]
        if compact:
            out["visual_summary"] = compact
    return out


def _trim_transcript_window(
    window: list[dict[str, Any]],
    *,
    start: float,
    end: float,
    budget_chars: int,
) -> list[dict[str, Any]]:
    """Filter ``window`` to ``[start, end]`` (inclusive) and downsample to fit ``budget_chars``."""
    in_range = [
        w for w in window
        if start <= float(w.get("start", -1.0)) <= end
    ]
    serialized = json.dumps(in_range, ensure_ascii=False, separators=(",", ":"))
    if len(serialized) <= budget_chars or not in_range:
        return in_range
    # Downsample by keeping every Nth entry until we fit.
    n = max(2, len(serialized) // max(1, budget_chars) + 1)
    return in_range[::n]


def _merge_and_dedup_clips(
    items: list[ClipDetectionItem], *, duration: float,
) -> list[ClipDetectionItem]:
    """Merge overlapping clip candidates. Keep the higher virality_score.

    Two clips are considered duplicates when their IoU
    (intersection / union) >= MERGE_IOU_THRESHOLD.
    """
    if not items:
        return []
    # Sort by virality_score desc -- the first clip in any cluster wins.
    sorted_items = sorted(items, key=lambda c: c.virality_score, reverse=True)
    kept: list[ClipDetectionItem] = []
    for cand in sorted_items:
        # Clamp to [0, duration] and ensure duration field is consistent.
        s = max(0.0, float(cand.start_time))
        e = min(float(duration or cand.end_time), float(cand.end_time))
        if e <= s:
            continue
        d = e - s
        is_dup = False
        for k in kept:
            inter = max(0.0, min(e, k.end_time) - max(s, k.start_time))
            union = max(e, k.end_time) - min(s, k.start_time)
            iou = inter / union if union > 0 else 0.0
            if iou >= MERGE_IOU_THRESHOLD:
                is_dup = True
                break
        if not is_dup:
            kept.append(
                ClipDetectionItem(
                    clip_index=cand.clip_index,
                    start_time=s,
                    end_time=e,
                    duration=d,
                    virality_score=cand.virality_score,
                    main_hook=cand.main_hook,
                    emotional_peak=cand.emotional_peak,
                    retention_reason=cand.retention_reason,
                    topics=list(cand.topics or []),
                    target_style=cand.target_style,
                )
            )
    return kept


# ---------------------------------------------------------------------------
# Highlight normalisation
# ---------------------------------------------------------------------------


def _normalise_clip_highlights(
    response: ClipDetectionResponse, *, duration: float
) -> ClipDetectionResponse:
    """Validate / clamp clip times for single continuous moments.

    Since clips are now single continuous windows (not montages), this function:
    * Ensures start_time < end_time and both are within [0, duration].
    * Ensures clip duration is within [CLIP_MIN_S, CLIP_MAX_S].
    * If highlight_segments is provided, normalises it to a single entry
      matching the clip window. If absent, synthesises one.
    * Drops clips that fall outside duration constraints after clamping.
    """
    src_dur = max(0.0, float(duration or 0.0))
    kept: list[ClipDetectionItem] = []

    for clip in response.clips:
        # Clamp to [0, source_duration].
        cs = max(0.0, float(clip.start_time))
        ce = float(clip.end_time)
        if src_dur > 0:
            cs = min(cs, src_dur)
            ce = min(ce, src_dur)
        if ce <= cs:
            continue

        clip_dur = ce - cs

        # Enforce duration bounds: trim clips that are too long, drop too short.
        if clip_dur > CLIP_MAX_S:
            # Trim to max duration from the start (keep the hook).
            ce = cs + CLIP_MAX_S
            clip_dur = CLIP_MAX_S
        if clip_dur < CLIP_MIN_S:
            # Try to extend forward if source allows.
            need = CLIP_MIN_S - clip_dur
            if src_dur > 0 and ce + need <= src_dur:
                ce += need
                clip_dur = ce - cs
            elif src_dur > 0 and cs - need >= 0:
                cs -= need
                clip_dur = ce - cs
            else:
                # Still too short — drop.
                continue

        clip.start_time = cs
        clip.end_time = ce
        clip.duration = clip_dur

        # Normalise highlight_segments to a single entry matching the window.
        clip.highlight_segments = [
            HighlightSegment(
                start=cs,
                end=ce,
                reason=(
                    clip.highlight_segments[0].reason
                    if clip.highlight_segments
                    else "continuous moment"
                ),
            )
        ]
        kept.append(clip)

    # Re-index clips contiguously.
    for i, c in enumerate(kept):
        c.clip_index = i
    response.clips = kept

    return response


# ---------------------------------------------------------------------------
# Mocks
# ---------------------------------------------------------------------------


def _is_mock() -> bool:
    return os.environ.get("MOCK_LLM", "0") == "1"


def _mock_clip_detection(*, duration: float, target: int) -> ClipDetectionResponse:
    """Generate ``target`` non-overlapping clips spread across ``duration``.

    Clips are single continuous moments in the 15-60s TikTok sweet spot.
    """
    target = max(1, min(target, 10))
    # Aim for ~30s clips (TikTok sweet spot), ensure 15<=dur<=60.
    desired_dur = max(CLIP_MIN_S, min(CLIP_MAX_S, duration / (target * 1.5)))
    items: list[ClipDetectionItem] = []
    cursor = 5.0
    for i in range(target):
        if cursor + desired_dur > duration - 1.0:
            break
        start = cursor
        end = cursor + desired_dur
        items.append(
            ClipDetectionItem(
                clip_index=i,
                start_time=start,
                end_time=end,
                duration=end - start,
                virality_score=round(8.5 - 0.3 * i, 2),
                main_hook=f"NUMBER: Mock hook #{i + 1} — something incredible happens here",
                emotional_peak="excitement builds to breaking point",
                retention_reason="curiosity_gap + visual_spectacle",
                topics=["mock", "fixture", f"clip-{i + 1}"],
                target_style="reaction",
                highlight_segments=[
                    HighlightSegment(start=start, end=end, reason="continuous moment"),
                ],
            )
        )
        cursor = end + 10.0
    if not items:
        # ensure at least one clip even for tiny durations
        clip_end = min(max(CLIP_MIN_S, duration), duration)
        items.append(
            ClipDetectionItem(
                clip_index=0,
                start_time=0.0,
                end_time=clip_end,
                duration=clip_end,
                virality_score=7.5,
                main_hook="SURPRISE: Mock single clip — unexpected moment",
                emotional_peak="curiosity peaks at the reveal",
                retention_reason="curiosity_gap",
                topics=["mock"],
                target_style="reaction",
                highlight_segments=[
                    HighlightSegment(start=0.0, end=clip_end, reason="continuous moment"),
                ],
            )
        )
    return ClipDetectionResponse(clips=items)


def _mock_edit_plan(clip: dict[str, Any]) -> EditPlan:
    """Deterministic edit plan for one clip."""
    idx = int(clip.get("clip_index", 0))
    clip_dur = max(0.1, float(clip.get("duration", clip.get("end_time", 15.0) - clip.get("start_time", 0.0))))
    # Two evenly-spaced narrative segments so the segment-aware TTS path has
    # something to work with under MOCK_LLM=1.
    half = clip_dur / 2.0
    mock_segments = [
        NarrativeSegment(
            start=0.0,
            end=half,
            text_vi="Đừng bỏ qua khoảnh khắc này.",
        ),
        NarrativeSegment(
            start=half,
            end=clip_dur,
            text_vi="Nó thay đổi mọi thứ.",
        ),
    ]
    return EditPlan(
        clip_index=idx,
        title=f"Viral hook #{idx + 1}",
        hook=str(clip.get("main_hook", "")),
        summary="Deterministic mock edit plan.",
        viral_angle="curiosity-gap",
        editing_style=EditingStyle(
            aggressive_pacing=True,
            dynamic_subtitles=True,
            fast_zoom_cuts=True,
            visual_overlays=False,
            pattern_interrupts=True,
            cinematic_sound_design=False,
        ),
        narrative_script_vi="Đừng bỏ qua khoảnh khắc này. Nó thay đổi mọi thứ.",
        narrative_segments=mock_segments,
        visual_effects=[
            {"type": "zoom_punch", "start": 2.0, "end": 2.3, "params": {"scale": 1.12}},
            {"type": "zoom_punch", "start": 8.0, "end": 8.3, "params": {"scale": 1.15}},
        ],
        subtitle_style=SubtitleStyle(
            font="Inter",
            size=72,
            primary_color="#FFFFFF",
            outline_color="#000000",
            outline_width=4,
            position="bottom",
            emphasis_color="#FFD400",
            word_highlight=True,
        ),
        pattern_interrupts=[
            {"at": 5.0, "kind": "flash", "params": {"duration": 0.08}},
        ],
        crop_plan=CropPlan(
            mode="track_face",
            keyframes=[
                CropKeyframe(t=0.0, x=0.5, y=0.5, w=0.0, h=0.0),
                CropKeyframe(t=15.0, x=0.5, y=0.5, w=0.0, h=0.0),
            ],
        ),
    )

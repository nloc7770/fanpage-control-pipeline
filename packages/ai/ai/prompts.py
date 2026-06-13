"""Canonical Qwen prompt templates.

These functions return ``list[dict]`` ready to pass to the OpenAI
chat-completions endpoint. The wording mirrors ``docs/llm-prompts.md`` -- if
you change anything here, update the doc too.
"""

from __future__ import annotations

import json
from typing import Any

# ---------------------------------------------------------------------------
# System prompt -- identical across templates.
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are a senior short-form video editor and viral content strategist. "
    "You only respond with valid JSON that matches the schema given in the user "
    "message. Do not include explanations, markdown, code fences, or any prose "
    "outside the JSON object. If you are uncertain, still return your best "
    "structured answer -- never return an empty body.\n/no_think"
)


def _system_message() -> dict[str, str]:
    return {"role": "system", "content": SYSTEM_PROMPT}


def _dumps(value: Any) -> str:
    """Serialize ``value`` to compact JSON suitable for embedding in a prompt."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


# ---------------------------------------------------------------------------
# 1. Viral clip detection
# ---------------------------------------------------------------------------


def viral_clip_detection_messages(
    transcript_segments: list[dict[str, Any]],
    video_duration: float,
    signals: dict[str, Any] | None = None,
    *,
    target_clip_count: int = 5,
    diarization: list[dict[str, Any]] | None = None,
    visual_summary: dict[str, Any] | None = None,
    rejection_reason: str | None = None,
) -> list[dict[str, str]]:
    """Build the messages for ``qwen.detect_clips``.

    ``signals`` is a free-form dict captured for backward compatibility with
    earlier callers; if it carries ``diarization`` / ``visual_summary`` we lift
    those into the dedicated slots.

    ``rejection_reason`` is set on retries: the previous LLM response failed a
    quality gate and the reason is prepended so the model tries harder.
    """
    signals = signals or {}
    diarization = diarization if diarization is not None else signals.get("diarization", [])
    visual_summary = (
        visual_summary if visual_summary is not None else signals.get("visual_summary", {})
    )
    user = (
        "TASK: Detect SINGLE CONTINUOUS MOMENTS from a long video that will\n"
        "perform as standalone TikTok/Reels clips. Each clip is ONE uncut window\n"
        "(start_time to end_time) — NOT a montage, NOT stitched segments.\n\n"
        "WHAT MAKES A VIRAL CLIP:\n"
        "- A clear HOOK in the first 3 seconds that stops the scroll\n"
        "- TENSION or buildup in the middle that keeps viewers watching\n"
        "- A PAYOFF at the end (reveal, punchline, emotional peak, resolution)\n"
        "- The viewer must feel something: shock, curiosity, laughter, awe, FOMO\n\n"
        "INPUT:\n"
        "- transcript_segments: JSON array of {start, end, text} covering the whole video.\n"
        "- diarization: JSON array of {speaker_id, segments: [{start, end}, ...]}.\n"
        "- visual_summary: JSON object summarising YOLO detections per second.\n"
        "- source_duration_s: total video duration in seconds.\n"
        "- target_clip_count: how many clips to return (default 5, max 10).\n\n"
        "DURATION RULES (TikTok sweet spot):\n"
        "- Each clip MUST be 15-60 seconds (duration = end_time - start_time).\n"
        "- Ideal: 20-45 seconds. Under 15s = too short to build tension.\n"
        "  Over 60s = viewers drop off before payoff.\n"
        "- The clip is a SINGLE CONTINUOUS WINDOW. No stitching, no jumping.\n\n"
        "HOOK FORMULA (main_hook MUST match one of these types):\n"
        "- QUESTION: poses a question the viewer needs answered\n"
        "- NUMBER: leads with a specific number/stat that shocks\n"
        "- SURPRISE: unexpected event or reveal in the opening\n"
        "- DIRECT ADDRESS: speaks directly to viewer ('you won't believe...')\n"
        "- PATTERN INTERRUPT: breaks expectations visually or verbally\n"
        "main_hook must be 10+ characters and clearly state the hook.\n\n"
        "VIRALITY SCORE RUBRIC (be honest, most clips are 5-7):\n"
        "- 1-3: Boring, no hook, no payoff, filler content\n"
        "- 4-6: Interesting topic but slow pacing or weak hook\n"
        "- 7-8: Strong hook + clear payoff, 50k+ views potential\n"
        "- 9-10: Viral gold — shock reveal, unexpected twist, emotional gut-punch,\n"
        "  or perfect comedic timing. Reserve for truly exceptional moments.\n\n"
        "RETENTION REASON (must cite at least one):\n"
        "- curiosity_gap: viewer NEEDS to know what happens next\n"
        "- emotional_shock: sudden emotional shift (joy->tears, calm->rage)\n"
        "- visual_spectacle: something visually stunning or rare\n"
        "- humor: genuinely funny moment or perfect comedic timing\n"
        "- fomo: viewer feels they'd miss out by scrolling away\n"
        "- relatability: viewer sees themselves in the situation\n"
        "retention_reason must explicitly name which metric(s) apply.\n\n"
        "CONSTRAINTS:\n"
        "- Each clip = one continuous window: start_time to end_time.\n"
        "- duration = end_time - start_time (MUST be 15.0 to 60.0 seconds).\n"
        "- start_time >= 0 and end_time <= source_duration_s.\n"
        "- Clips MUST NOT overlap with each other.\n"
        "- highlight_segments is OPTIONAL — if provided, use a single entry\n"
        "  matching [start_time, end_time] with a reason describing the moment.\n"
        "- Prefer moments where something CHANGES (status quo -> disruption).\n"
        "- Avoid: intros, outros, filler, repetitive explanations, dead air.\n\n"
        "SCHEMA (return exactly this shape):\n"
        '{"clips":[{"clip_index":int,"start_time":float,"end_time":float,'
        '"duration":float,"virality_score":float,"main_hook":string,'
        '"emotional_peak":string,"retention_reason":string,"topics":[string],'
        '"target_style":string,'
        '"highlight_segments":[{"start":float,"end":float,"reason":string}]}]}\n\n'
        "FIELD NOTES:\n"
        "- start_time: where the clip begins in the source video (seconds).\n"
        "- end_time: where the clip ends in the source video (seconds).\n"
        "- duration: end_time - start_time (must be 15.0-60.0).\n"
        "- main_hook: 10+ chars, must match one of the 5 hook formulas above.\n"
        "- retention_reason: must cite one of: curiosity_gap, emotional_shock,\n"
        "  visual_spectacle, humor, fomo, relatability.\n"
        "- highlight_segments: optional, single entry [{start, end, reason}]\n"
        "  matching the clip window. Used for backward compat only.\n\n"
        "EXAMPLES:\n"
        "GOOD (single continuous moment with clear arc):\n"
        '{"clips":[{"clip_index":0,"start_time":234.5,"end_time":272.0,'
        '"duration":37.5,"virality_score":8.5,'
        '"main_hook":"NUMBER: 200-pound fish drags the boat for 45 minutes straight",'
        '"emotional_peak":"The moment the line almost snaps and everyone screams",'
        '"retention_reason":"curiosity_gap + visual_spectacle — viewer needs to see if they land it",'
        '"topics":["fishing","giant-catch","struggle"],'
        '"target_style":"reaction",'
        '"highlight_segments":[{"start":234.5,"end":272.0,"reason":"continuous moment: epic fight with giant fish"}]'
        "}]}\n\n"
        "BAD — DO NOT do this (will be rejected):\n"
        '{"clips":['
        '{"clip_index":0,"start_time":0,"end_time":90,"duration":90,"virality_score":5,'
        '"main_hook":"fish","emotional_peak":"","retention_reason":"interesting",'
        '"topics":[],"target_style":"","highlight_segments":[]}]}\n'
        "Why bad: duration=90 exceeds 60s max. main_hook is 4 chars (need 10+).\n"
        "virality_score=5 is lazy default. retention_reason doesn't cite a metric.\n"
        "emotional_peak is empty. No hook formula type specified.\n\n"
        f"DATA:\n"
        f"transcript_segments: {_dumps(transcript_segments)}\n"
        f"diarization: {_dumps(diarization)}\n"
        f"visual_summary: {_dumps(visual_summary)}\n"
        f"source_duration_s: {video_duration}\n"
        f"target_clip_count: {target_clip_count}\n"
    )
    if rejection_reason:
        user = (
            f"PREVIOUS RESPONSE WAS REJECTED — {rejection_reason}\n"
            "Try harder: read the transcript carefully, pick REAL highlight moments, "
            "write specific main_hook and emotional_peak text, assign genuine virality scores.\n\n"
        ) + user
    return [_system_message(), {"role": "user", "content": user}]


# ---------------------------------------------------------------------------
# 2. Edit planning
# ---------------------------------------------------------------------------


def edit_plan_messages(
    clip: dict[str, Any],
    transcript_window: list[dict[str, Any]],
    yolo_hints: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Build the messages for ``qwen.plan_edit`` (per detected clip)."""
    clip_start = float(clip.get("start_time", 0.0)) if isinstance(clip, dict) else 0.0
    user = (
        "TASK: Produce a detailed edit plan for one short-form clip.\n\n"
        "OUTPUT FORMAT: You MUST return exactly ONE top-level JSON OBJECT (not\n"
        "an array) with all of the fields listed in SCHEMA below. The\n"
        "`narrative_segments` field is one field INSIDE that object -- do NOT\n"
        "return a bare array of segments as your top-level response.\n\n"
        "INPUT:\n"
        "- clip: a ClipDetectionItem (start_time, end_time, hook, target_style, ...).\n"
        "- clip_transcript: the segment-level transcript inside the clip window,\n"
        "  each item is {start, end, text} with ABSOLUTE source times.\n"
        "- visual_summary_in_window: YOLO detections inside the window.\n"
        '- target_aspect_ratio: "9:16".\n\n'
        "CONSTRAINTS:\n"
        '- crop_plan.mode must be one of "track_face", "center", "smart", "static".\n'
        "- pattern_interrupts are spaced at least 2.5 seconds apart.\n"
        "- subtitle_style.size is in pixels, intended for a 1080x1920 canvas.\n"
        "- editing_style booleans must be coherent with target_style.\n"
        "- narrative_segments MUST mirror the input clip_transcript: for each\n"
        "  segment in clip_transcript, produce exactly ONE NarrativeSegment with\n"
        "  the SAME start/end times (relative to the CLIP, i.e. subtract\n"
        f"  clip.start_time ({clip_start}) from the transcript's absolute times),\n"
        "  and `text_vi` = a natural Vietnamese rewrite of that segment's English\n"
        "  text. Number of output segments MUST equal number of input segments\n"
        "  (or one less, see the merge rule below).\n"
        "- Each text_vi MUST be MAX 60 characters. Shorter is better.\n"
        "  If the English is long-winded, condense ruthlessly. TikTok = punchy.\n"
        "- The total speaking duration of text_vi should fit within (end - start)\n"
        "  when spoken at normal pace (~3-4 Vietnamese syllables/second).\n"
        "- You MAY merge two adjacent transcript segments if they are very short\n"
        "  (<1.5s each) and the merged Vietnamese reads more naturally. Mark the\n"
        "  merged segment's start/end as the union [first.start, second.end].\n"
        "- All segment times MUST satisfy 0 <= start < end <= clip.duration.\n"
        "- Vietnamese only (tiếng Việt, có dấu). Do NOT keep English. Do NOT use\n"
        "  English quotes. Natural spoken register, social-media tone.\n"
        "- REWRITE, do NOT translate literally. Capture meaning + emotion in\n"
        "  idiomatic Vietnamese suitable for TikTok/Reels. If a direct word-for-word\n"
        "  translation sounds unnatural or wrong in Vietnamese, rephrase entirely.\n"
        "- TONE: Use spoken Vietnamese. Prefer 'mình', 'họ', 'chúng ta' over formal\n"
        "  'tôi' where it fits the context. Avoid stiff Hán-Việt compounds when a\n"
        "  plain everyday word works equally well (e.g. 'thực ra' > 'thực chất',\n"
        "  'to lớn' > 'hùng vĩ' unless truly epic).\n"
        "- TIKTOK SLANG: Use viral Vietnamese expressions naturally:\n"
        "  'không tin nổi', 'quá điên', 'chắc chắn', 'đỉnh của chóp',\n"
        "  'sốc nặng', 'ảo thật sự', 'điên rồ', 'khó tin'.\n"
        "  These create familiarity with the TikTok audience.\n"
        "- HOOK-FIRST RULE: The first 2 words of the FIRST text_vi MUST grab\n"
        "  attention immediately. No warm-up, no context-setting.\n"
        "  BAD first words: 'Đây là', 'Hôm nay', 'Chúng ta', 'Trong video'\n"
        "  GOOD first words: 'Không tin', 'Quá điên', '200 ký', 'Sốc nặng',\n"
        "  'Nhìn này', 'Chưa ai', 'Lần đầu'\n"
        "- EXAMPLES of BAD vs GOOD Vietnamese hooks:\n"
        "  BAD: 'Đây là một con cá rất lớn mà chúng tôi bắt được'\n"
        "       (boring intro, no urgency, too formal)\n"
        "  GOOD: 'Không tin nổi — con cá này nặng hơn người lớn!'\n"
        "       (pattern interrupt + number + shock)\n"
        "  BAD: 'Hôm nay chúng tôi đi câu cá ở sông'\n"
        "       (zero hook, sounds like a diary entry)\n"
        "  GOOD: 'Quá điên! Kéo 45 phút mà nó vẫn không chịu thua'\n"
        "       (slang + number + tension)\n"
        "- HOOK RULE: The FIRST narrative_segment's text_vi MUST be a strong hook --\n"
        "  trigger curiosity or emotion. NEVER start with a generic introduction.\n"
        "  Good openers: a surprising fact, a number, a question, or a bold claim.\n"
        "- LITERAL-TRANSLATION TRAPS -- these English terms are commonly\n"
        "  mistranslated; use the correct Vietnamese meaning:\n"
        "  * 'game fish' = cá thể thao / cá săn câu (NOT 'cá chơi game')\n"
        "  * 'White boy' (fishing nickname) = thằng bé trắng / cái tên biệt danh\n"
        "    (NOT 'cậu bé da trắng' / 'cậu bé trắng')\n"
        "  * 'well-documented' = được ghi nhận rõ ràng (NOT 'được ghi chép tốt')\n"
        "  * 'accounts' (stories) = lời kể / câu chuyện (NOT 'tài khoản')\n"
        "  * 'testimony/testimonies' = lời chứng / lời kể (context-dependent)\n"
        "  * Sport/fishing idioms must be re-rendered for meaning, not word-for-word.\n"
        "- EXAMPLES of bad vs good rewrite:\n"
        "  EN: 'tarpon are well-documented game fish'\n"
        "  BAD: 'Cá tarpon được biết đến là loài cá chơi game' (WRONG -- 'game fish'\n"
        "       is a fishing term meaning prized sport fish, NOT video-game fish)\n"
        "  GOOD: 'Cá tarpon là loài cá săn câu nổi tiếng nhất thế giới'\n"
        "  ---\n"
        "  EN: 'You won\\'t believe what happened next'\n"
        "  BAD: 'Bạn sẽ không tin những gì xảy ra tiếp theo'\n"
        "  GOOD: 'Chuyện xảy ra sau đó khiến tôi sốc thật sự'\n"
        "  ---\n"
        "  EN: 'The biggest fish I\\'ve ever seen'\n"
        "  BAD: 'Con cá lớn nhất tôi đã từng nhìn thấy'\n"
        "  GOOD: 'Con cá to nhất trong đời mình -- chưa bao giờ thấy cái gì như vậy'\n"
        "- narrative_script_vi (the legacy single-string field) MUST be the\n"
        "  concatenation of all text_vi values joined with a single space.\n"
        "- narrative_segments[i].text_vi MUST be a faithful REWRITE (not literal\n"
        "  translation) of the corresponding source segment text\n"
        "  (clip_transcript[i].text) -- preserve meaning, names, numbers; rewrite\n"
        "  wording for social-media but do not invent new content. The pair\n"
        "  (clip_transcript[i].text, narrative_segments[i].text_vi) is shown to\n"
        "  viewers as bilingual subtitles (English bottom, Vietnamese top), so the\n"
        "  two lines MUST convey the same meaning.\n"
        "- title, hook, summary, viral_angle SHOULD ALSO be in Vietnamese.\n"
        "- You MUST also produce a `fb_caption_package` object with:\n"
        "  - `caption`: 2-3 short VIETNAMESE lines suitable as a Facebook reel\n"
        "    caption. Hook-first. No more than 1 emoji. No quotes around the\n"
        "    whole thing. The first line MUST grab attention (NUMBERS, surprise,\n"
        "    question, or direct address). Total length <= 180 characters.\n"
        "  - `hashtags`: 8-12 lowercase Vietnamese-friendly hashtags WITHOUT\n"
        "    the `#` prefix (the renderer adds it). Mix general (e.g. fishing,\n"
        "    sinhton) and specific (e.g. cakhonglo, motminhgiuarung). No spaces\n"
        "    inside tags.\n"
        "  - `cta`: ONE short Vietnamese question (<=80 chars) that asks viewers\n"
        "    to comment. Empty string allowed.\n"
        '  - `niche`: one of "fishing", "survival", "camping", "trap", "other".\n'
        "  The `fb_caption_package` field is REQUIRED on the top-level object --\n"
        "  do NOT omit it. Repeat: include `fb_caption_package` in your response.\n\n"
        "SCHEMA (return EXACTLY this top-level object shape):\n"
        '{"clip_index":int,"title":string,"hook":string,"summary":string,'
        '"viral_angle":string,"editing_style":{"aggressive_pacing":bool,'
        '"dynamic_subtitles":bool,"fast_zoom_cuts":bool,"visual_overlays":bool,'
        '"pattern_interrupts":bool,"cinematic_sound_design":bool},'
        '"narrative_script_vi":string,'
        '"narrative_segments":[{"start":float,"end":float,"text_vi":string}],'
        '"visual_effects":[{"type":string,'
        '"start":float,"end":float,"params":{}}],"subtitle_style":{"font":string,'
        '"size":int,"primary_color":string,"outline_color":string,'
        '"outline_width":int,"position":"top"|"middle"|"bottom",'
        '"emphasis_color":string,"word_highlight":bool},"pattern_interrupts":'
        '[{"at":float,"kind":string,"params":{}}],"crop_plan":{"mode":'
        '"track_face"|"center"|"smart"|"static","keyframes":[{"t":float,'
        '"x":float,"y":float,"w":float,"h":float}]},'
        '"fb_caption_package":{"caption":string,"hashtags":[string],'
        '"cta":string,"niche":string}}\n\n'
        "EXAMPLE (illustrative only -- do NOT copy these texts; shows how\n"
        "narrative_segments NESTS inside the full edit-plan object):\n"
        "  Given clip.start_time=10.0, clip.end_time=25.0, and\n"
        "  clip_transcript=[\n"
        '    {"start":10.0,"end":13.0,"text":"You won\'t believe this."},\n'
        '    {"start":13.0,"end":19.5,"text":"The fish jumped right out of the river."},\n'
        '    {"start":19.5,"end":25.0,"text":"It was the biggest one we\'ve ever caught."}\n'
        "  ]\n"
        "  -> A valid response looks like:\n"
        '  {"clip_index":0,"title":"...","hook":"...","summary":"...",\n'
        '   "viral_angle":"...","editing_style":{...},\n'
        '   "narrative_script_vi":"Bạn sẽ không tin nổi điều này. ...",\n'
        '   "narrative_segments":[\n'
        '     {"start":0.0,"end":3.0,"text_vi":"Bạn sẽ không tin nổi điều này."},\n'
        '     {"start":3.0,"end":9.5,"text_vi":"Con cá nhảy vọt ra khỏi mặt sông."},\n'
        '     {"start":9.5,"end":15.0,"text_vi":"Đó là con cá to nhất mà chúng tôi từng bắt được."}\n'
        "   ],\n"
        '   "visual_effects":[...],"subtitle_style":{...},'
        '"pattern_interrupts":[...],"crop_plan":{...},\n'
        '   "fb_caption_package":{"caption":"...","hashtags":["..."],'
        '"cta":"...","niche":"fishing"}}\n'
        "  (Note: segment start/end are relative to the CLIP -- subtract\n"
        "  clip.start_time from the transcript's absolute times. 10.0 -> 0.0,\n"
        "  13.0 -> 3.0, 25.0 -> 15.0, etc.)\n\n"
        "DATA:\n"
        f"clip: {_dumps(clip)}\n"
        f"clip_transcript: {_dumps(transcript_window)}\n"
        f"visual_summary_in_window: {_dumps(yolo_hints or {})}\n"
        'target_aspect_ratio: "9:16"\n'
    )
    return [_system_message(), {"role": "user", "content": user}]


# ---------------------------------------------------------------------------
# 3. Narrative rewrite (VI)
# ---------------------------------------------------------------------------


def narrative_rewrite_vi_messages(
    transcript_window: list[dict[str, Any]],
    hook: str,
    *,
    clip: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Build the messages for ``qwen.rewrite_narrative``."""
    if clip is None:
        clip = {"main_hook": hook}
    user = (
        "TASK: Rewrite the clip's core message as a punchy Vietnamese voiceover "
        "suitable for a 9:16 short-form video. Match the clip's emotional tone "
        "and preserve any specific facts from the transcript.\n\n"
        "INPUT:\n"
        "- clip: ClipDetectionItem.\n"
        "- transcript_window: the word-level transcript inside the clip window.\n\n"
        "CONSTRAINTS:\n"
        "- Output a single string under 280 characters.\n"
        "- Vietnamese only. Natural spoken register, not formal written prose.\n"
        "- Open with a hook that mirrors clip.main_hook.\n\n"
        "SCHEMA:\n"
        '{"clip_index":int,"narrative_script_vi":string}\n\n'
        "DATA:\n"
        f"clip: {_dumps(clip)}\n"
        f"transcript_window: {_dumps(transcript_window)}\n"
    )
    return [_system_message(), {"role": "user", "content": user}]


# ---------------------------------------------------------------------------
# 4. Subtitle condensation
# ---------------------------------------------------------------------------


def subtitle_condensation_messages(
    transcript_words_window: list[dict[str, Any]],
    style: dict[str, Any] | None = None,
    *,
    clip: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Build the messages for ``qwen.condense_subs``."""
    user = (
        "TASK: Convert a word-level transcript window into short, punchy "
        "on-screen subtitle lines suitable for 9:16 short-form video.\n\n"
        "INPUT:\n"
        "- clip: ClipDetectionItem.\n"
        "- words: array of {start, end, word} inside the clip window.\n\n"
        "CONSTRAINTS:\n"
        "- Each line is at most 4 words.\n"
        "- Each line spans at most 1.6 seconds.\n"
        "- start/end times must align to the word timings provided.\n"
        "- emphasis_words is a subset of the line's words; mark hook-words and numbers.\n\n"
        "SCHEMA:\n"
        '{"clip_index":int,"lines":[{"start":float,"end":float,"text":string,'
        '"emphasis_words":[string]}]}\n\n'
        "DATA:\n"
        f"clip: {_dumps(clip or {})}\n"
        f"words: {_dumps(transcript_words_window)}\n"
        f"style_hint: {_dumps(style or {})}\n"
    )
    return [_system_message(), {"role": "user", "content": user}]


# ---------------------------------------------------------------------------
# 5. JSON repair
# ---------------------------------------------------------------------------


def json_repair_messages(broken: str, schema_hint: Any) -> list[dict[str, str]]:
    """Build the messages for ``qwen.repair_json``.

    ``schema_hint`` may be a pydantic model class (we extract its JSON schema),
    a dict (used verbatim), or any other object (stringified).
    """
    try:
        from pydantic import BaseModel

        if isinstance(schema_hint, type) and issubclass(schema_hint, BaseModel):
            schema_repr = _dumps(schema_hint.model_json_schema())
        elif isinstance(schema_hint, dict):
            schema_repr = _dumps(schema_hint)
        else:
            schema_repr = str(schema_hint)
    except Exception:
        schema_repr = str(schema_hint)

    user = (
        "TASK: Repair the JSON below so it matches the schema. Do not invent "
        "values; fix structural issues: stray trailing commas, missing brackets, "
        "smart quotes, markdown code fences, or extra prose. If a required field "
        "is missing, set it to a sensible empty value (empty string, 0, empty list).\n\n"
        f"SCHEMA:\n{schema_repr}\n\n"
        f"BROKEN:\n{broken}\n\n"
        'RESPONSE SHAPE:\n{ "data": { ... the repaired object ... } }\n'
    )
    return [_system_message(), {"role": "user", "content": user}]

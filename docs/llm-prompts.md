# LLM Prompt Templates

All prompts target the Qwen3-Coder-Next GGUF Q5_K_M model via the
OpenAI-compatible endpoint at `QWEN_BASE_URL`. The system prompt is identical
across templates and pins JSON-only output. The user prompt is task-specific.

The output is parsed against the corresponding pydantic model in
`packages/shared-py/shared_py/llm_contracts.py`. On parse failure, the caller
routes through the JSON Repair prompt below before giving up.

## Common system prompt

```
You are a senior short-form video editor and viral content strategist.
You only respond with valid JSON that matches the schema given in the user
message. Do not include explanations, markdown, code fences, or any prose
outside the JSON object. If you are uncertain, still return your best
structured answer -- never return an empty body.
```

---

## 1. Viral clip detection

**Task name:** `qwen.detect_clips`
**Response model:** `ClipDetectionResponse`

**User prompt template:**

```
TASK: Identify the most viral-worthy moments in a long-form video.

INPUT:
- transcript_word_level: a JSON array of {start, end, word, speaker?} objects.
- diarization: a JSON array of {speaker_id, segments: [{start, end}, ...]}.
- visual_summary: a JSON object summarising YOLO detections per second
  (e.g. dominant objects, scene cuts, face presence).
- source_duration_s: the total video duration in seconds.
- target_clip_count: how many clips to return (default 5, max 10).

CONSTRAINTS:
- HARD DURATION RULE: each clip MUST be between 60.0 and 180.0 seconds long
  (i.e. 1 to 3 minutes). end_time - start_time MUST satisfy 60.0 <= duration <= 180.0.
  Never return clips shorter than 60s or longer than 180s; extend or trim the
  window to keep duration in range.
- The reported `duration` field MUST equal end_time - start_time, in [60.0, 180.0].
- Clips must not overlap.
- start_time and end_time must align to word boundaries from the transcript.
- start_time >= 0 and end_time <= source_duration_s.
- virality_score is on a 0..10 scale; reserve 9-10 for truly exceptional moments.

SCHEMA (return exactly this shape):
{
  "clips": [
    {
      "clip_index": int,             // 0-based, dense
      "start_time": float,           // seconds
      "end_time": float,
      "duration": float,             // end_time - start_time
      "virality_score": float,       // 0..10
      "main_hook": string,           // the hook line, verbatim or paraphrased
      "emotional_peak": string,      // what emotion peaks here
      "retention_reason": string,    // why a viewer keeps watching
      "topics": [string, ...],
      "target_style": string         // e.g. "fast_cut", "talking_head"
    }
  ]
}

DATA:
transcript_word_level: {{TRANSCRIPT_JSON}}
diarization: {{DIARIZATION_JSON}}
visual_summary: {{VISUAL_SUMMARY_JSON}}
source_duration_s: {{DURATION}}
target_clip_count: {{TARGET_CLIP_COUNT}}
```

---

## 2. Edit planning

**Task name:** `qwen.plan_edit`
**Response model:** `EditPlan`

Run once per detected clip.

**User prompt template:**

```
TASK: Produce a detailed edit plan for one short-form clip.

INPUT:
- clip: a ClipDetectionItem (start_time, end_time, hook, target_style, ...).
- clip_transcript: the word-level transcript inside the clip window.
- visual_summary_in_window: YOLO detections inside the window.
- target_aspect_ratio: "9:16".

CONSTRAINTS:
- crop_plan.mode must be one of "track_face", "center", "smart", "static".
- pattern_interrupts are spaced at least 2.5 seconds apart.
- subtitle_style.size is in pixels, intended for a 1080x1920 canvas.
- editing_style booleans must be coherent with target_style.
- You MUST also produce a `fb_caption_package` object with:
  - `caption`: 2-3 short VIETNAMESE lines suitable as a Facebook reel caption.
    Hook-first. No more than 1 emoji. No quotes around the whole thing.
    The first line MUST grab attention (NUMBERS, surprise, question, or
    direct address). Total length <= 180 characters.
  - `hashtags`: 8-12 lowercase Vietnamese-friendly hashtags WITHOUT the `#`
    prefix (the renderer adds it). Mix general (e.g. fishing, sinhton) and
    specific (e.g. cakhonglo, motminhgiuarung). No spaces inside tags.
  - `cta`: ONE short Vietnamese question (<=80 chars) asking viewers to
    comment. Empty string allowed.
  - `niche`: one of "fishing", "survival", "camping", "trap", "other".
  The `fb_caption_package` field is REQUIRED on the top-level object -- do
  NOT omit it.

SCHEMA:
{
  "clip_index": int,
  "title": string,
  "hook": string,
  "summary": string,
  "viral_angle": string,
  "editing_style": {
    "aggressive_pacing": bool,
    "dynamic_subtitles": bool,
    "fast_zoom_cuts": bool,
    "visual_overlays": bool,
    "pattern_interrupts": bool,
    "cinematic_sound_design": bool
  },
  "narrative_script_vi": string,     // short VI voiceover (may be empty)
  "visual_effects": [
    { "type": string, "start": float, "end": float, "params": {} }
  ],
  "subtitle_style": {
    "font": string, "size": int,
    "primary_color": string, "outline_color": string, "outline_width": int,
    "position": "top"|"middle"|"bottom",
    "emphasis_color": string, "word_highlight": bool
  },
  "pattern_interrupts": [
    { "at": float, "kind": string, "params": {} }
  ],
  "crop_plan": {
    "mode": "track_face"|"center"|"smart"|"static",
    "keyframes": [{ "t": float, "x": float, "y": float, "w": float, "h": float }]
  },
  "fb_caption_package": {
    "caption": string,                  // 2-3 short VI lines, hook-first, <=180 chars
    "hashtags": [string, ...],          // 8-12 lowercase tags, no `#` prefix
    "cta": string,                      // <=80 chars VI question or ""
    "niche": "fishing"|"survival"|"camping"|"trap"|"other"
  }
}

DATA:
clip: {{CLIP_JSON}}
clip_transcript: {{CLIP_TRANSCRIPT_JSON}}
visual_summary_in_window: {{VISUAL_SUMMARY_JSON}}
target_aspect_ratio: "9:16"
```

---

## 3. Narrative rewrite (VI)

**Task name:** `qwen.rewrite_narrative`
**Response model:** `NarrativeRewriteVIResponse`

**User prompt template:**

```
TASK: Rewrite the clip's core message as a punchy Vietnamese voiceover
suitable for a 9:16 short-form video. Match the clip's emotional tone and
preserve any specific facts from the transcript.

INPUT:
- clip: ClipDetectionItem.
- transcript_window: the word-level transcript inside the clip window.

CONSTRAINTS:
- Output a single string under 280 characters.
- Vietnamese only. Natural spoken register, not formal written prose.
- Open with a hook that mirrors clip.main_hook.

SCHEMA:
{
  "clip_index": int,
  "narrative_script_vi": string
}

DATA:
clip: {{CLIP_JSON}}
transcript_window: {{CLIP_TRANSCRIPT_JSON}}
```

---

## 4. Subtitle condensation

**Task name:** `qwen.condense_subs`
**Response model:** `SubtitleCondensationResponse`

**User prompt template:**

```
TASK: Convert a word-level transcript window into short, punchy on-screen
subtitle lines suitable for 9:16 short-form video.

INPUT:
- clip: ClipDetectionItem.
- words: array of {start, end, word} inside the clip window.

CONSTRAINTS:
- Each line is at most 4 words.
- Each line spans at most 1.6 seconds.
- start/end times must align to the word timings provided.
- emphasis_words is a subset of the line's words; mark hook-words and numbers.

SCHEMA:
{
  "clip_index": int,
  "lines": [
    { "start": float, "end": float, "text": string,
      "emphasis_words": [string, ...] }
  ]
}

DATA:
clip: {{CLIP_JSON}}
words: {{WORDS_JSON}}
```

---

## 5. JSON repair

**Task name:** `qwen.repair_json`
**Response model:** `JsonRepairResponse`

Used as a fallback whenever a previous response failed `model_validate`.

**User prompt template:**

```
TASK: Repair the JSON below so it matches the schema. Do not invent values;
fix structural issues: stray trailing commas, missing brackets, smart quotes,
markdown code fences, or extra prose. If a required field is missing, set it
to a sensible empty value (empty string, 0, empty list).

SCHEMA:
{{SCHEMA_JSON}}

BROKEN:
{{BROKEN_TEXT}}

RESPONSE SHAPE:
{ "data": { ... the repaired object ... } }
```

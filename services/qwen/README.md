# services/qwen

**Status:** placeholder. Owner: Phase-2 workers agent.

Wraps the Qwen LLM via the OpenAI-compatible endpoint at `QWEN_BASE_URL`,
model `QWEN_MODEL`. Tasks:

- `qwen.detect_clips` -- given transcript + diarization + YOLO summary,
  return `ClipDetectionResponse`.
- `qwen.plan_edit` -- given one detected clip + supporting context, return
  one `EditPlan`. Run in parallel across clips.
- `qwen.rewrite_narrative` -- VI voiceover script for a clip.
- `qwen.condense_subs` -- short, punchy on-screen subtitle lines.
- `qwen.repair_json` -- fallback when a previous task returned malformed JSON.

All five prompts are documented in `docs/llm-prompts.md` and parsed against
the pydantic models in `shared_py.llm_contracts`.

After `qwen.detect_clips`:
1. Write `analysis_results` row (emotional peaks, viral moments, topic shifts,
   retention signals, summary) and one `clips` row per detected clip
   (`status=planned`). Emit `clip.planned` for each.
2. Fan out `qwen.plan_edit` for each clip.

After all `qwen.plan_edit` tasks resolve, enqueue `render.prepare_assets`,
which in turn enqueues `render.render_clip` per clip.

`MOCK_LLM=1` returns deterministic fixtures for all five prompts.

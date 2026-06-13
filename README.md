# packages/ai

**Status:** placeholder. Owner: Phase-2 workers agent.

Shared AI utility package consumed by the workers (and possibly the API for
synchronous explanations / titles). It must expose:

- A thin OpenAI-compatible HTTP client targeting `QWEN_BASE_URL` / `QWEN_MODEL`
  (async + sync flavors).
- Retry + timeout + JSON-mode helpers.
- A `parse_or_repair(text, schema)` helper that tries to parse JSON against a
  pydantic model from `shared_py.llm_contracts`, and on failure routes through
  the `qwen.repair_json` task.
- A prompt registry that loads the canonical templates from
  `docs/llm-prompts.md` (or re-declares them here) and exposes typed
  `render_*` functions.

No business logic -- only reusable primitives. Workers import from here.

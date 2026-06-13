"""Best-effort JSON parsing helpers.

Order of fallbacks:

1. ``json.loads`` raw input.
2. Strip Markdown code fences (```json ... ```), leading/trailing prose and
   smart quotes, then retry.
3. Locate the first balanced ``{...}`` or ``[...]`` substring and parse that.
4. (Optional) ask Qwen to repair via the JSON-repair prompt.

``parse_or_repair`` exposes the full flow including the LLM step; tests
generally exercise :func:`try_parse_json` which only does the local fixups.
"""

from __future__ import annotations

import json
import re
from typing import Any

from loguru import logger
from pydantic import BaseModel, ValidationError

_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*([\s\S]*?)\s*```", re.MULTILINE)
_SMART_QUOTES = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'"})


def _strip_fences(text: str) -> str:
    m = _FENCE_RE.search(text)
    if m:
        return m.group(1).strip()
    return text.strip()


def _extract_balanced(text: str, open_ch: str, close_ch: str) -> str | None:
    start = text.find(open_ch)
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def try_parse_json(text: str) -> Any | None:
    """Return parsed JSON or ``None`` (never raises).

    Tries the raw string, then a few common LLM-output fixups: code fences,
    smart quotes, and balanced-bracket extraction.
    """
    if not isinstance(text, str):
        return None
    # 1. raw
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2. fence/quote cleanup
    cleaned = _strip_fences(text).translate(_SMART_QUOTES)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 3. balanced extraction (objects first, then arrays)
    for opener, closer in (("{", "}"), ("[", "]")):
        candidate = _extract_balanced(cleaned, opener, closer)
        if candidate is None:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            # Try stripping trailing commas inside the candidate.
            try:
                return json.loads(_strip_trailing_commas(candidate))
            except json.JSONDecodeError:
                continue

    # 4. last resort: strip trailing commas from the whole cleaned blob.
    try:
        return json.loads(_strip_trailing_commas(cleaned))
    except json.JSONDecodeError:
        return None


def _strip_trailing_commas(text: str) -> str:
    """Remove ``,\\s*[}\\]]`` patterns, respecting string literals."""
    out: list[str] = []
    in_str = False
    esc = False
    i = 0
    while i < len(text):
        ch = text[i]
        if in_str:
            out.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            i += 1
            continue
        if ch == ",":
            # Look ahead past whitespace.
            j = i + 1
            while j < len(text) and text[j] in " \t\r\n":
                j += 1
            if j < len(text) and text[j] in "}]":
                # Skip the comma.
                i += 1
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def parse_or_repair(
    text: str,
    schema: type[BaseModel],
    qwen_client: Any | None = None,
    *,
    max_repair_attempts: int = 2,
) -> BaseModel:
    """Parse ``text`` against ``schema``; on failure ask Qwen to repair.

    ``qwen_client`` must expose ``chat(messages, response_format="json", ...)``.
    If ``None`` we skip the LLM repair phase and raise the local error.
    """
    from ai.prompts import json_repair_messages  # local import to avoid cycles

    parsed = try_parse_json(text)
    last_err: Exception | None = None
    if parsed is not None:
        try:
            return schema.model_validate(parsed)
        except ValidationError as exc:
            last_err = exc
            logger.warning("local parse_or_repair validation failed: {}", exc)
    else:
        last_err = ValueError("text is not parseable JSON")

    if qwen_client is None:
        assert last_err is not None
        raise last_err

    current = text
    for attempt in range(max_repair_attempts):
        msgs = json_repair_messages(broken=current, schema_hint=schema)
        current = qwen_client.chat(msgs, response_format="json", temperature=0.0)
        parsed = try_parse_json(current)
        # Repair prompt asks for {"data": <repaired>}; unwrap if present.
        if isinstance(parsed, dict) and "data" in parsed:
            parsed = parsed["data"]
        if parsed is not None:
            try:
                return schema.model_validate(parsed)
            except ValidationError as exc:
                last_err = exc
                logger.warning(
                    "parse_or_repair attempt {} still invalid: {}", attempt + 1, exc
                )
                continue
        last_err = ValueError("repair output was not parseable JSON")

    assert last_err is not None
    raise last_err

"""AI utility package: Qwen client + prompts + JSON repair."""

from __future__ import annotations

from ai.json_repair import parse_or_repair, try_parse_json
from ai.prompts import (
    edit_plan_messages,
    json_repair_messages,
    narrative_rewrite_vi_messages,
    subtitle_condensation_messages,
    viral_clip_detection_messages,
)
from ai.qwen_client import AsyncQwenClient, QwenClient, QwenClientConfig

__all__ = [
    "AsyncQwenClient",
    "QwenClient",
    "QwenClientConfig",
    "edit_plan_messages",
    "json_repair_messages",
    "narrative_rewrite_vi_messages",
    "parse_or_repair",
    "subtitle_condensation_messages",
    "try_parse_json",
    "viral_clip_detection_messages",
]

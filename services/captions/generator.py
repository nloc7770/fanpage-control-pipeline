"""LLM-based caption generation for Facebook Reels.

Uses the same QwenClient as the rest of the pipeline. Generates a
title, caption, and hashtags for a clip given the page's language and niche.
"""

from __future__ import annotations

import os
from typing import Any

from loguru import logger
from pydantic import BaseModel

from ai.qwen_client import QwenClient


# ---------------------------------------------------------------------------
# Response schema
# ---------------------------------------------------------------------------


class CaptionResponse(BaseModel):
    title: str
    caption: str
    hashtags: list[str]


# --- caption-template:v2 (engagement+depth) ----------------------------------
# Unique anchor for this module's template work. Do not collapse with other
# prompt sections owned by adjacent agents (beat schedule, hook rules, clip
# length, topics). If you need to extend, add a new anchor block below.

BANNED_HASHTAGS: frozenset[str] = frozenset(
    {"fyp", "viral", "trending", "foryou", "foryoupage", "xuhuong"}
)
MAX_HASHTAGS: int = 7
MIN_HASHTAGS: int = 5


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def _caption_messages(
    *,
    clip_title: str | None,
    clip_hook: str | None,
    clip_topics: list[str],
    clip_duration: float,
    page_niche: str | None,
    page_language: str,
    source_title: str | None,
    channel_name: str | None,
) -> list[dict[str, str]]:
    """Build chat messages for caption generation.

    Template (caption-template:v2):
        Line 1 -> SAME hook as visual first segment (repeat verbatim).
        Lines 2-4 -> max 3 short value-add lines, each <80 chars.
        Line  -> CTA question with leading "❓".
        Final -> 5-7 niche-relevant hashtags; banned: fyp/viral/trending.
    """
    lang_instruction = (
        "Viết hoàn toàn bằng tiếng Việt (có dấu). Giọng văn tự nhiên, thân thiện, phù hợp Facebook Reels."
        if page_language == "vi"
        else "Write entirely in English. Natural, conversational tone suitable for Facebook Reels."
    )

    # --- caption-template:v2 — structured caption rules -----------------
    structured_rules_vi = (
        "BẮT BUỘC theo TEMPLATE 4 KHỐI (không trộn lẫn, không bỏ khối nào):\n"
        "  KHỐI 1 (dòng 1): LẶP LẠI Y NGUYÊN hook của clip (xem field `clip_hook`).\n"
        "    Đây là cùng câu xuất hiện ở segment hình đầu tiên — viewer thấy 2 lần\n"
        "    (trong video + trên caption) để củng cố context. KHÔNG paraphrase.\n"
        "  KHỐI 2 (dòng 2-4): TỐI ĐA 3 dòng giá trị thêm. Mỗi dòng < 80 ký tự.\n"
        "    Bổ sung chi tiết, ngữ cảnh, hoặc insight — KHÔNG kể lại nội dung clip.\n"
        "  KHỐI 3 (1 dòng): Bắt đầu bằng '❓ ' rồi đặt 1 câu hỏi mở để viewer\n"
        "    comment. Không câu hỏi đóng (yes/no). Tối đa 80 ký tự.\n"
        "  KHỐI 4 (dòng cuối): 5-7 hashtag niche-cụ-thể, mỗi tag có '#', cách nhau\n"
        "    bằng 1 space. CẤM: #fyp, #viral, #trending, #xuhuong, #foryou\n"
        "    (Facebook đã giảm reach các tag này). Tag phải liên quan trực tiếp\n"
        "    niche của trang ('"
        + (page_niche or "general")
        + "').\n"
        "RÀNG BUỘC chung:\n"
        "- Không sao chép tiêu đề YouTube nguyên văn.\n"
        "- Không đề cập 'video gốc', 'kênh gốc', hay nguồn nội dung.\n"
        "- Không bịa thông tin không có trong clip.\n"
        "- Tối đa 1-2 emoji TOÀN BỘ caption.\n"
    )

    structured_rules_en = (
        "MANDATORY 4-BLOCK TEMPLATE (do not merge or skip):\n"
        "  BLOCK 1 (line 1): REPEAT the clip hook VERBATIM (see `clip_hook`).\n"
        "    Same line as the visual first segment — viewer sees it twice\n"
        "    (in video + on caption) for context. DO NOT paraphrase.\n"
        "  BLOCK 2 (lines 2-4): AT MOST 3 value-add lines. Each line < 80 chars.\n"
        "    Add detail, context, or insight — do NOT retell the clip.\n"
        "  BLOCK 3 (1 line): Start with '❓ ' then ask one open-ended question to\n"
        "    invite comments. No yes/no questions. Max 80 chars.\n"
        "  BLOCK 4 (last line): 5-7 niche-specific hashtags, each prefixed with\n"
        "    '#', separated by single spaces. BANNED: #fyp, #viral, #trending,\n"
        "    #foryou (Facebook deprioritizes). Tags must directly match the page\n"
        "    niche ('"
        + (page_niche or "general")
        + "').\n"
        "General constraints:\n"
        "- Do NOT copy the YouTube title verbatim.\n"
        "- Do NOT reference 'original video', 'original channel', or the source.\n"
        "- Do NOT fabricate information not in the clip.\n"
        "- 1-2 emoji max across the whole caption.\n"
    )

    caption_rules = structured_rules_vi if page_language == "vi" else structured_rules_en

    hashtag_note = (
        "Đặt vào field `hashtags` 5-7 tag (KHÔNG ký tự '#', không space, lowercase).\n"
        "Renderer sẽ tự thêm '#'. Cấm: fyp, viral, trending, xuhuong, foryou."
        if page_language == "vi"
        else
        "Put 5-7 tags in `hashtags` (NO '#' prefix, no spaces, lowercase).\n"
        "Renderer adds '#'. Banned: fyp, viral, trending, foryou."
    )

    title_rules = (
        "Tiêu đề ngắn (tối đa 10 từ), hấp dẫn, phù hợp Facebook Reels. Không sao chép tiêu đề YouTube."
        if page_language == "vi"
        else
        "Short title (max 10 words), compelling, suitable for Facebook Reels. Do not copy the YouTube title."
    )

    # Two fully-worked VN examples covering the niches the prompt is most
    # frequently called for. They show the LITERAL string the model should put
    # in the `caption` field, including the 4 blocks separated by newlines.
    examples_block = (
        "EXAMPLE 1 (Vietnamese, fitness niche; clip_hook='1 tuần tập tay sai cách'):\n"
        '{"title": "Sai lầm 90% người mới tập tay đều mắc",\n'
        ' "caption": "1 tuần tập tay sai cách\\n'
        'Cơ tay không to lên mà còn đau khớp khuỷu.\\n'
        'Lỗi nằm ở góc cùi chỏ — không phải tạ nặng.\\n'
        'Fix trong 30s ở cuối video.\\n'
        '❓ Bạn từng bị đau khuỷu khi tập tay chưa?",\n'
        ' "hashtags": ["taptay", "gymvietnam", "fitnesstips", "bicepworkout", "gymform", "personaltrainer"]}\n\n'
        "EXAMPLE 2 (Vietnamese, outdoor/survival niche; clip_hook='Đốt lửa giữa rừng mưa'):\n"
        '{"title": "Mẹo đốt lửa khi gỗ ướt sũng",\n'
        ' "caption": "Đốt lửa giữa rừng mưa\\n'
        'Bí quyết: tìm lõi khô bên trong cành gãy.\\n'
        'Vỏ bạch dương cháy được cả khi ướt.\\n'
        'Mồi lửa từ bông gòn + vaseline — bắt cháy 100%.\\n'
        '❓ Mẹo sinh tồn nào bạn muốn xem tiếp theo?",\n'
        ' "hashtags": ["sinhton", "bushcraftvn", "datrai", "kynangsinhton", "phuotrung", "outdoorvietnam"]}\n'
    )

    user = (
        f"TASK: Generate a Facebook Reels caption package for a short-form video clip.\n"
        f"# caption-template:v2 (DO NOT merge with sections owned by other agents:\n"
        f"#   beat-schedule, hook-rules, clip-length, topics)\n\n"
        f"LANGUAGE: {lang_instruction}\n\n"
        f"CLIP INFO:\n"
        f"- clip_hook (REPEAT IN BLOCK 1 VERBATIM): {clip_hook or '(none)'}\n"
        f"- topics: {', '.join(clip_topics) if clip_topics else '(none)'}\n"
        f"- duration_s: {clip_duration:.1f}\n"
        f"- page_niche: {page_niche or 'general'}\n"
        f"- source_title (DO NOT copy verbatim): {source_title or '(none)'}\n"
        f"- channel_name (DO NOT mention): {channel_name or '(none)'}\n\n"
        f"TITLE RULES:\n{title_rules}\n\n"
        f"CAPTION RULES:\n{caption_rules}\n"
        f"HASHTAG FIELD RULES:\n{hashtag_note}\n\n"
        f"SCHEMA (return exactly this shape — NO markdown, NO code fences):\n"
        f'{{"title": string, "caption": string, "hashtags": [string]}}\n'
        f"  - `caption` string MUST contain BLOCKS 1-3 separated by '\\n'.\n"
        f"  - Hashtags (BLOCK 4) go in the `hashtags` array, NOT in `caption`.\n"
        f"  - Renderer will append '#tag #tag ...' to caption when posting.\n\n"
        f"{examples_block}"
    )

    system = (
        "You are a social media content writer specializing in Facebook Reels captions. "
        "You only respond with valid JSON matching the schema. "
        "No markdown, no code fences, no prose outside the JSON object."
    )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_caption(
    clip: Any,
    page: Any,
    content_source: Any | None,
) -> dict[str, str | list[str]]:
    """Generate title, caption, and hashtags for a clip.

    Args:
        clip: database.models.Clip ORM row (or dict with same fields).
        page: database.models.FacebookPage ORM row.
        content_source: database.models.ContentSource ORM row, or None.

    Returns:
        {"title": str, "caption": str, "hashtags": list[str]}
    """
    # Support both ORM rows and plain dicts.
    def _get(obj: Any, attr: str, default: Any = None) -> Any:
        if isinstance(obj, dict):
            return obj.get(attr, default)
        return getattr(obj, attr, default)

    clip_title: str | None = _get(clip, "title")
    clip_hook: str | None = _get(clip, "main_hook")
    clip_topics: list[str] = _get(clip, "topics") or []
    clip_duration: float = float(_get(clip, "duration") or 0.0)

    page_niche: str | None = _get(page, "niche")
    page_language: str = _get(page, "language") or "vi"

    source_title: str | None = _get(content_source, "source_title") if content_source else None
    channel_name: str | None = _get(content_source, "channel_name") if content_source else None

    if _is_mock():
        return _mock_caption(page_language=page_language, clip_hook=clip_hook)

    messages = _caption_messages(
        clip_title=clip_title,
        clip_hook=clip_hook,
        clip_topics=clip_topics,
        clip_duration=clip_duration,
        page_niche=page_niche,
        page_language=page_language,
        source_title=source_title,
        channel_name=channel_name,
    )

    with QwenClient() as qwen:
        result = qwen.chat_json(messages, CaptionResponse, max_tokens=512)

    # --- caption-template:v2 — hashtag post-processing ----------------
    # 1) normalize (strip '#', spaces, lowercase)
    # 2) drop banned over-used tags (FB deprioritizes)
    # 3) hard-cap to MAX_HASHTAGS (=7)
    # 4) pad with niche fallbacks if below MIN_HASHTAGS (=5)
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in (result.hashtags or []):
        if not raw:
            continue
        tag = str(raw).strip().lstrip("#").replace(" ", "").lower()
        if not tag or tag in BANNED_HASHTAGS or tag in seen:
            continue
        cleaned.append(tag)
        seen.add(tag)
        if len(cleaned) >= MAX_HASHTAGS:
            break

    if len(cleaned) < MIN_HASHTAGS:
        # Niche-aware fallbacks; avoid banned set.
        fallbacks = [
            page_niche or "reels",
            "shortform",
            "reelsvietnam" if page_language == "vi" else "reels",
            "khoanhkhac" if page_language == "vi" else "moment",
        ]
        for fb in fallbacks:
            if not fb:
                continue
            tag = fb.strip().lstrip("#").replace(" ", "").lower()
            if tag in BANNED_HASHTAGS or tag in seen:
                continue
            cleaned.append(tag)
            seen.add(tag)
            if len(cleaned) >= MIN_HASHTAGS:
                break
    hashtags = cleaned

    logger.info(
        "generate_caption: clip={} lang={} title={!r} hashtags={}",
        _get(clip, "id"),
        page_language,
        result.title[:60] if result.title else "",
        len(hashtags),
    )

    return {
        "title": result.title or "",
        "caption": result.caption or "",
        "hashtags": hashtags,
    }


# ---------------------------------------------------------------------------
# Mock
# ---------------------------------------------------------------------------


def _is_mock() -> bool:
    return os.environ.get("MOCK_LLM", "0") == "1"


def _mock_caption(
    *, page_language: str, clip_hook: str | None
) -> dict[str, str | list[str]]:
    if page_language == "vi":
        return {
            "title": "Khoảnh khắc không thể tin được",
            "caption": (
                f"{clip_hook or 'Điều này thực sự bất ngờ'} 😮\n"
                "Xem đến cuối để hiểu tại sao mọi người đều sốc."
            ),
            "hashtags": ["viral", "reels", "khoanhkhac", "bongda", "tinhte"],
        }
    return {
        "title": "You won't believe this moment",
        "caption": (
            f"{clip_hook or 'This is absolutely unbelievable'} 😮\n"
            "Watch till the end to see why everyone is shocked."
        ),
        "hashtags": ["viral", "reels", "moment", "trending", "shortform"],
    }

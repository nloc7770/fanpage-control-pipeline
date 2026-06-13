"""LLM-based "human-style" caption + hashtag generator for image posts.

Uses the same QwenClient as the rest of the pipeline.

Public API
----------
* ``generate_caption(*, topic, niche, language) -> dict``
  Returns ``{"caption": str, "hashtags": list[str]}``.
"""

from __future__ import annotations

import os

from loguru import logger
from pydantic import BaseModel

from ai.qwen_client import QwenClient


# ---------------------------------------------------------------------------
# Response schema
# ---------------------------------------------------------------------------


class CaptionResponse(BaseModel):
    caption: str
    hashtags: list[str]


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def _caption_messages(
    *,
    topic: str,
    niche: str,
    language: str,
) -> list[dict[str, str]]:
    """Build chat messages for human-style image post caption generation."""

    if language == "vi":
        lang_instruction = (
            "Viết hoàn toàn bằng tiếng Việt (có dấu). "
            "Giọng văn tự nhiên, thân thiện, như người thật đang chia sẻ trải nghiệm."
        )
        voice_rules = (
            "- Dùng ngôi thứ nhất: 'mình', 'tôi', 'mình thấy', 'hôm nay mình...'\n"
            "- Cho phép 1 lỗi nhỏ tự nhiên mỗi ~30 từ (ví dụ: thiếu dấu phẩy, viết tắt thông thường)\n"
            "- Câu ngắn xen kẽ câu dài — không đều nhau\n"
            "- Rải 0-2 emoji tự nhiên TRONG câu, không dồn cuối bài\n"
            "- Nhúng 1-2 hashtag TRONG đoạn văn, phần còn lại để cuối\n"
            "- Kết thúc bằng câu hỏi mở để tăng tương tác\n"
            "- KHÔNG dùng: 'Mua ngay', 'ĐỪng bỏ lỡ', chữ hoa quá nhiều, >7 hashtag\n"
            "- KHÔNG viết theo kiểu marketing/quảng cáo"
        )
        hashtag_rules = (
            "3-7 hashtag liên quan đến chủ đề và niche. "
            "Không có dấu cách trong hashtag. Không có ký tự #. "
            "Nhúng 1-2 hashtag vào trong caption, phần còn lại để trong mảng hashtags."
        )
        example = (
            '{"caption": "Hôm nay mình thử cái này lần đầu và thật sự bất ngờ 😮 '
            "#fitlife — cảm giác sau buổi tập khác hẳn so với trước. "
            "Mình hay bỏ qua bước này lắm, giờ mới thấy quan trọng. "
            'Bạn có hay làm vậy không?", '
            '"hashtags": ["fitlife", "suckhoe", "lifestyle", "thoiquen", "thethao"]}'
        )
    else:
        lang_instruction = (
            "Write entirely in English. "
            "Natural, conversational tone — like a real person sharing their experience."
        )
        voice_rules = (
            "- Use first-person voice: 'I', 'me', 'I noticed', 'today I...'\n"
            "- Allow 1 small natural imperfection per ~30 words (missing comma, casual abbreviation)\n"
            "- Mix short and long sentences — uneven rhythm feels authentic\n"
            "- Sprinkle 0-2 emojis naturally IN the text, not all at the end\n"
            "- Embed 1-2 hashtags INLINE in the caption body, rest go at the end\n"
            "- End with an open-ended question to drive engagement\n"
            "- DO NOT use: 'Buy now', 'Don't miss out', excessive caps, >7 hashtags\n"
            "- DO NOT write in a marketing/advertising style"
        )
        hashtag_rules = (
            "3-7 hashtags relevant to the topic and niche. "
            "No spaces inside tags. No # prefix. "
            "Embed 1-2 hashtags inline in the caption, rest go in the hashtags array."
        )
        example = (
            '{"caption": "Tried this for the first time today and honestly surprised 😮 '
            "#fitness — the feeling after the session was completely different. "
            "I used to skip this step all the time, now I get why it matters. "
            'Do you do this regularly?", '
            '"hashtags": ["fitness", "health", "lifestyle", "habits", "workout"]}'
        )

    user = (
        f"TASK: Generate a Facebook image post caption for a photo about the topic below.\n\n"
        f"LANGUAGE: {lang_instruction}\n\n"
        f"TOPIC: {topic}\n"
        f"NICHE: {niche}\n\n"
        f"VOICE & STYLE RULES:\n{voice_rules}\n\n"
        f"HASHTAG RULES:\n{hashtag_rules}\n\n"
        f"SCHEMA (return exactly this shape):\n"
        f'{{\"caption\": string, \"hashtags\": [string]}}\n\n'
        f"EXAMPLE ({language} output):\n{example}\n"
    )

    system = (
        "You are a social media content writer who creates authentic, human-sounding "
        "Facebook image post captions. You only respond with valid JSON matching the schema. "
        "No markdown, no code fences, no prose outside the JSON object."
    )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def generate_caption(
    *,
    topic: str,
    niche: str,
    language: str = "vi",
) -> dict[str, str | list[str]]:
    """Generate a human-style caption and hashtags for an image post.

    Parameters
    ----------
    topic:
        The subject of the image (e.g. "morning workout routine").
    niche:
        The page niche (e.g. "fitness", "cooking", "travel").
    language:
        ``"vi"`` for Vietnamese, ``"en"`` for English.

    Returns
    -------
    dict
        ``{"caption": str, "hashtags": list[str]}``
    """
    if _is_mock():
        return _mock_caption(topic=topic, niche=niche, language=language)

    messages = _caption_messages(topic=topic, niche=niche, language=language)

    with QwenClient() as qwen:
        result = qwen.chat_json(messages, CaptionResponse, max_tokens=512)

    # Clamp hashtags to 3-7.
    hashtags = list(result.hashtags or [])
    if len(hashtags) > 7:
        hashtags = hashtags[:7]
    if len(hashtags) < 3:
        fallbacks = [niche or "lifestyle", "reels", "viral"]
        for fb in fallbacks:
            if fb and fb not in hashtags:
                hashtags.append(fb)
            if len(hashtags) >= 3:
                break

    logger.info(
        "generate_caption: topic={!r} niche={!r} lang={} hashtags={}",
        topic[:60],
        niche,
        language,
        len(hashtags),
    )

    return {
        "caption": result.caption or "",
        "hashtags": hashtags,
    }


# ---------------------------------------------------------------------------
# Mock
# ---------------------------------------------------------------------------


def _is_mock() -> bool:
    return os.environ.get("MOCK_LLM", "0") == "1"


def _mock_caption(
    *, topic: str, niche: str, language: str
) -> dict[str, str | list[str]]:
    if language == "vi":
        return {
            "caption": (
                f"Hôm nay mình thử {topic} và thật sự ấn tượng 😊 "
                f"#suckhoe — cảm giác rất khác so với trước. "
                f"Mình hay bỏ qua điều này, giờ mới thấy quan trọng. "
                f"Bạn có hay làm vậy không?"
            ),
            "hashtags": [niche or "lifestyle", "suckhoe", "cuocsong", "viral", "reels"],
        }
    return {
        "caption": (
            f"Tried {topic} today and honestly blown away 😊 "
            f"#{niche or 'lifestyle'} — the experience was completely different. "
            f"I used to skip this, now I see why it matters. "
            f"Do you do this regularly?"
        ),
        "hashtags": [niche or "lifestyle", "health", "life", "viral", "reels"],
    }

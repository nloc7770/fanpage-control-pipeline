"""pollinations.ai async image generator.

Public API
----------
* ``generate_image(prompt, *, width, height, seed) -> bytes``
* ``humanize_prompt(topic, niche, language) -> str``
"""

from __future__ import annotations

import os
import random
from urllib.parse import quote

import httpx
from loguru import logger


# ---------------------------------------------------------------------------
# Candid / realism phrase banks
# ---------------------------------------------------------------------------

_CANDID_PHRASES_EN = [
    "iPhone photo",
    "slight grain",
    "golden hour lighting",
    "real moment",
    "no perfect framing",
    "candid shot",
    "natural light",
    "authentic feel",
    "unposed",
    "documentary style",
    "soft bokeh",
    "handheld camera",
]

_CANDID_PHRASES_VI = [
    "ảnh chụp điện thoại",
    "ánh sáng tự nhiên",
    "khoảnh khắc thật",
    "không dàn dựng",
    "phong cách tài liệu",
    "hạt nhiễu nhẹ",
    "ánh vàng buổi chiều",
]

_STYLE_SUFFIX = "photorealistic, high detail, no text, no watermark"


def humanize_prompt(topic: str, niche: str, language: str = "vi") -> str:
    """Build a candid-style image prompt from a topic + niche.

    Appends a random selection of realism phrases so the generated image
    looks like a genuine moment rather than a polished stock photo.
    """
    phrases = _CANDID_PHRASES_VI if language == "vi" else _CANDID_PHRASES_EN
    # Pick 3-4 random candid phrases without repetition.
    chosen = random.sample(phrases, k=min(4, len(phrases)))
    candid_str = ", ".join(chosen)

    base = f"{topic}, {niche} lifestyle"
    return f"{base}, {candid_str}, {_STYLE_SUFFIX}"


# ---------------------------------------------------------------------------
# Image generation
# ---------------------------------------------------------------------------

_POLLINATIONS_BASE = "https://image.pollinations.ai/prompt"
_DEFAULT_TIMEOUT = 120.0  # seconds


async def generate_image(
    prompt: str,
    *,
    width: int = 1200,
    height: int = 628,
    seed: int | None = None,
) -> bytes:
    """Fetch a JPEG image from pollinations.ai and return raw bytes.

    Parameters
    ----------
    prompt:
        Text description of the desired image.
    width / height:
        Output dimensions. Default 1200×628 (Facebook landscape 1.91:1).
    seed:
        Optional integer seed for reproducibility. If None, a random seed
        is chosen so each call produces a fresh image.

    Returns
    -------
    bytes
        Raw JPEG image data.

    Raises
    ------
    httpx.HTTPStatusError
        If pollinations.ai returns a non-2xx status.
    httpx.TimeoutException
        If the request exceeds ``_DEFAULT_TIMEOUT`` seconds.
    """
    if seed is None:
        seed = random.randint(1, 2**31 - 1)

    encoded_prompt = quote(prompt, safe="")
    url = (
        f"{_POLLINATIONS_BASE}/{encoded_prompt}"
        f"?width={width}&height={height}&nologo=true&seed={seed}&model=flux"
    )

    timeout = float(os.environ.get("IMAGE_GEN_TIMEOUT_S", str(_DEFAULT_TIMEOUT)))

    logger.info(
        "generate_image: prompt_len={} size={}x{} seed={} timeout={}s",
        len(prompt),
        width,
        height,
        seed,
        timeout,
    )

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()

    content_type = response.headers.get("content-type", "")
    logger.info(
        "generate_image: received {} bytes content_type={}",
        len(response.content),
        content_type,
    )
    return response.content

"""Async + sync Qwen client wrapping the OpenAI-compatible chat endpoint.

The remote service exposes ``POST {QWEN_BASE_URL}/chat/completions`` with the
standard OpenAI payload. Both clients share a single :class:`QwenClientConfig`
and use ``httpx`` for transport.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, TypeVar

import httpx
from loguru import logger
from pydantic import BaseModel, ValidationError

from ai.json_repair import try_parse_json
from ai.prompts import json_repair_messages

T = TypeVar("T", bound=BaseModel)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class QwenContextOverflowError(RuntimeError):
    """Raised when the Qwen server rejects a request because the prompt
    (messages + max_tokens) exceeds the server's context window.

    Callers should respond by chunking the input -- see
    ``services.qwen.runner.detect_clips`` for the canonical pattern.
    """

    def __init__(self, status_code: int, body: str, prompt_chars: int) -> None:
        super().__init__(
            f"Qwen context overflow (HTTP {status_code}, prompt_chars={prompt_chars}): "
            f"{body[:300]}"
        )
        self.status_code = status_code
        self.body = body
        self.prompt_chars = prompt_chars


_CONTEXT_OVERFLOW_MARKERS = (
    "context",
    "too many tokens",
    "exceeds",
    "n_ctx",
    "maximum context length",
    "tokenization",
    "token limit",
)


def _looks_like_context_overflow(body: str) -> bool:
    """True if a 400 response body matches a context-overflow error."""
    if not body:
        return False
    lowered = body.lower()
    return any(marker in lowered for marker in _CONTEXT_OVERFLOW_MARKERS)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class QwenClientConfig:
    base_url: str
    model: str
    timeout_s: float = 120.0
    max_tokens: int = 4096
    api_key: str = "not-needed"  # local Qwen servers ignore this

    @classmethod
    def from_env(cls) -> QwenClientConfig:
        return cls(
            base_url=os.environ.get("QWEN_BASE_URL", "http://localhost:8001/v1"),
            model=os.environ.get("QWEN_MODEL", "qwen3-coder-next-q5km"),
            timeout_s=float(os.environ.get("QWEN_TIMEOUT_S", "120")),
            max_tokens=int(os.environ.get("QWEN_MAX_TOKENS", "4096")),
            api_key=os.environ.get("QWEN_API_KEY", "not-needed"),
        )


def _payload(
    cfg: QwenClientConfig,
    messages: list[dict[str, str]],
    *,
    response_format: str,
    temperature: float,
    max_tokens: int | None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": cfg.model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens or cfg.max_tokens,
    }
    if response_format == "json":
        body["response_format"] = {"type": "json_object"}
    return body


def _extract_text(resp_body: dict[str, Any]) -> str:
    choices = resp_body.get("choices") or []
    if not choices:
        raise RuntimeError(f"Qwen response missing choices: {resp_body!r}")
    msg = choices[0].get("message") or {}
    content = msg.get("content")
    if not isinstance(content, str):
        raise RuntimeError(f"Qwen choice missing content: {choices[0]!r}")
    return content


# ---------------------------------------------------------------------------
# Sync client
# ---------------------------------------------------------------------------


class QwenClient:
    """Synchronous Qwen client used inside Celery tasks (prefork pool)."""

    def __init__(self, config: QwenClientConfig | None = None) -> None:
        self.config = config or QwenClientConfig.from_env()
        self._client: httpx.Client | None = None

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.config.base_url,
                timeout=self.config.timeout_s,
                headers={"Authorization": f"Bearer {self.config.api_key}"},
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> QwenClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ---- public ----------------------------------------------------------

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        response_format: str = "json",
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> str:
        """Return the raw assistant text."""
        body = _payload(
            self.config,
            messages,
            response_format=response_format,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        resp = self._http().post("/chat/completions", json=body)
        if resp.status_code >= 400:
            prompt_chars = sum(len(m.get("content", "")) for m in messages)
            logger.error(
                "Qwen HTTP {} body={!r} prompt_chars={}",
                resp.status_code,
                resp.text[:1000],
                prompt_chars,
            )
            if resp.status_code == 400 and _looks_like_context_overflow(resp.text):
                raise QwenContextOverflowError(
                    status_code=resp.status_code,
                    body=resp.text,
                    prompt_chars=prompt_chars,
                )
            resp.raise_for_status()
        return _extract_text(resp.json())

    def chat_json(
        self,
        messages: list[dict[str, str]],
        schema: type[T],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        repair_attempts: int = 2,
    ) -> T:
        """Call ``chat`` then validate against ``schema``, with up to two repairs.

        On a :class:`pydantic.ValidationError` (or :class:`json.JSONDecodeError`)
        we re-prompt Qwen with the JSON repair template; on success the parsed
        payload is wrapped in ``{"data": ...}`` so we unwrap before validating.
        """
        text = self.chat(
            messages,
            response_format="json",
            temperature=temperature,
            max_tokens=max_tokens,
        )
        last_err: Exception | None = None
        last_text: str = text
        for attempt in range(repair_attempts + 1):
            parsed = try_parse_json(text)
            last_text = text
            if parsed is not None:
                try:
                    return schema.model_validate(parsed)
                except ValidationError as exc:
                    last_err = exc
                    logger.warning(
                        "Qwen JSON validation failed (attempt {}): {}", attempt, exc
                    )
            else:
                last_err = ValueError("Qwen returned non-JSON content")
                logger.warning(
                    "Qwen returned non-JSON (attempt {}): {!r}", attempt, text[:200]
                )

            if attempt == repair_attempts:
                break
            repair_msgs = json_repair_messages(broken=text, schema_hint=schema)
            text = self.chat(
                repair_msgs,
                response_format="json",
                temperature=0.0,
                max_tokens=max_tokens,
            )
            # Unwrap the {"data": ...} envelope produced by the repair template.
            unwrapped = try_parse_json(text)
            if isinstance(unwrapped, dict) and "data" in unwrapped:
                text = json.dumps(unwrapped["data"], ensure_ascii=False)

        assert last_err is not None
        # Attach the last raw text so callers can run their own recovery (e.g.
        # the plan_edit wrap-bare-list fallback). Pydantic ValidationError
        # otherwise hides the actual model output.
        try:
            setattr(last_err, "last_raw_text", last_text)
        except Exception:
            pass
        raise last_err


# ---------------------------------------------------------------------------
# Async client (used by API for inline LLM helpers, if any)
# ---------------------------------------------------------------------------


class AsyncQwenClient:
    """Async sibling of :class:`QwenClient`."""

    def __init__(self, config: QwenClientConfig | None = None) -> None:
        self.config = config or QwenClientConfig.from_env()
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.config.base_url,
                timeout=self.config.timeout_s,
                headers={"Authorization": f"Bearer {self.config.api_key}"},
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> AsyncQwenClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        response_format: str = "json",
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> str:
        body = _payload(
            self.config,
            messages,
            response_format=response_format,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        resp = await self._http().post("/chat/completions", json=body)
        if resp.status_code >= 400:
            prompt_chars = sum(len(m.get("content", "")) for m in messages)
            logger.error(
                "Qwen HTTP {} body={!r} prompt_chars={}",
                resp.status_code,
                resp.text[:1000],
                prompt_chars,
            )
            if resp.status_code == 400 and _looks_like_context_overflow(resp.text):
                raise QwenContextOverflowError(
                    status_code=resp.status_code,
                    body=resp.text,
                    prompt_chars=prompt_chars,
                )
            resp.raise_for_status()
        return _extract_text(resp.json())

    async def chat_json(
        self,
        messages: list[dict[str, str]],
        schema: type[T],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        repair_attempts: int = 2,
    ) -> T:
        text = await self.chat(
            messages,
            response_format="json",
            temperature=temperature,
            max_tokens=max_tokens,
        )
        last_err: Exception | None = None
        for attempt in range(repair_attempts + 1):
            parsed = try_parse_json(text)
            if parsed is not None:
                try:
                    return schema.model_validate(parsed)
                except ValidationError as exc:
                    last_err = exc
            else:
                last_err = ValueError("Qwen returned non-JSON content")

            if attempt == repair_attempts:
                break
            repair_msgs = json_repair_messages(broken=text, schema_hint=schema)
            text = await self.chat(
                repair_msgs,
                response_format="json",
                temperature=0.0,
                max_tokens=max_tokens,
            )
            unwrapped = try_parse_json(text)
            if isinstance(unwrapped, dict) and "data" in unwrapped:
                text = json.dumps(unwrapped["data"], ensure_ascii=False)

        assert last_err is not None
        # Attach the last raw text so callers can run their own recovery (e.g.
        # the plan_edit wrap-bare-list fallback). Pydantic ValidationError
        # otherwise hides the actual model output.
        try:
            setattr(last_err, "last_raw_text", last_text)
        except Exception:
            pass
        raise last_err

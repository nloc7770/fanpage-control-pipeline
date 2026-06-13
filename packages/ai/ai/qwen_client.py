"""Async + sync Qwen client wrapping the OpenAI-compatible chat endpoint.

The remote service exposes ``POST {QWEN_BASE_URL}/chat/completions`` with the
standard OpenAI payload. Both clients share a single :class:`QwenClientConfig`
and use ``httpx`` for transport.

Retry & failover
-----------------
The client is designed for a 2-worker nginx load-balancer setup. When both
workers are busy the LB returns 502/503/504; the client retries with
exponential backoff (5s, 15s, 30s). Connection errors and read timeouts also
trigger retries with their own schedules. Each Celery task should instantiate
its own client (or use the context-manager pattern) to avoid shared state
across concurrent requests.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from typing import Any, TypeVar

import httpx
from loguru import logger
from pydantic import BaseModel, ValidationError

from ai.json_repair import try_parse_json
from ai.prompts import json_repair_messages

T = TypeVar("T", bound=BaseModel)


# ---------------------------------------------------------------------------
# Retry configuration
# ---------------------------------------------------------------------------

# HTTP status codes that indicate the load balancer has no available workers.
_LB_RETRY_STATUSES: frozenset[int] = frozenset({502, 503, 504})

# Backoff schedule (seconds) for LB errors (502/503/504). Length = max retries.
_LB_BACKOFF_SCHEDULE: tuple[float, ...] = (5.0, 15.0, 30.0)

# Read timeout retries (LLM generation can legitimately take minutes).
_TIMEOUT_MAX_RETRIES: int = 1
_TIMEOUT_RETRY_DELAY_S: float = 5.0

# Connection error retries (nginx briefly unreachable during reload, etc.).
_CONN_ERROR_MAX_RETRIES: int = 2
_CONN_ERROR_RETRY_DELAY_S: float = 3.0


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

_DEFAULT_CONNECT_TIMEOUT_S = 10.0
_DEFAULT_READ_TIMEOUT_S = 600.0


@dataclass(slots=True)
class QwenClientConfig:
    base_url: str
    model: str
    timeout_s: float = 600.0
    connect_timeout_s: float = _DEFAULT_CONNECT_TIMEOUT_S
    read_timeout_s: float = _DEFAULT_READ_TIMEOUT_S
    max_tokens: int = 4096
    api_key: str = "not-needed"  # local Qwen servers ignore this

    @classmethod
    def from_env(cls) -> QwenClientConfig:
        return cls(
            base_url=os.environ.get("QWEN_BASE_URL", "http://localhost:8001/v1"),
            model=os.environ.get("QWEN_MODEL", "qwen3.6:27b"),
            timeout_s=float(os.environ.get("QWEN_TIMEOUT_S", "600")),
            connect_timeout_s=float(
                os.environ.get("QWEN_CONNECT_TIMEOUT_S", str(_DEFAULT_CONNECT_TIMEOUT_S))
            ),
            read_timeout_s=float(
                os.environ.get("QWEN_READ_TIMEOUT_S", str(_DEFAULT_READ_TIMEOUT_S))
            ),
            max_tokens=int(os.environ.get("QWEN_MAX_TOKENS", "4096")),
            api_key=os.environ.get("QWEN_API_KEY", "not-needed"),
        )

    def httpx_timeout(self) -> httpx.Timeout:
        """Build a structured httpx.Timeout from config values."""
        return httpx.Timeout(
            connect=self.connect_timeout_s,
            read=self.read_timeout_s,
            write=self.connect_timeout_s,  # writes are small JSON payloads
            pool=self.connect_timeout_s,
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
    """Synchronous Qwen client used inside Celery tasks (prefork pool).

    Each instance maintains its own httpx.Client with a connection pool.
    For Celery prefork workers, instantiate one client per task invocation
    (via the context-manager pattern) to avoid cross-request state leakage.
    """

    def __init__(self, config: QwenClientConfig | None = None) -> None:
        self.config = config or QwenClientConfig.from_env()
        self._client: httpx.Client | None = None

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.config.base_url,
                timeout=self.config.httpx_timeout(),
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
        """Return the raw assistant text, with retry on transient failures."""
        body = _payload(
            self.config,
            messages,
            response_format=response_format,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        prompt_chars = sum(len(m.get("content", "")) for m in messages)

        # Track retries across error categories independently.
        lb_attempts = 0
        timeout_attempts = 0
        conn_attempts = 0
        total_attempt = 0

        while True:
            total_attempt += 1
            t0 = time.monotonic()
            try:
                resp = self._http().post("/chat/completions", json=body)
                elapsed = time.monotonic() - t0

                # --- Success path ---
                if resp.status_code < 400:
                    if total_attempt > 1:
                        logger.info(
                            "Qwen request succeeded on attempt {} "
                            "(response_time={:.1f}s prompt_chars={})",
                            total_attempt,
                            elapsed,
                            prompt_chars,
                        )
                    else:
                        logger.debug(
                            "Qwen response_time={:.1f}s prompt_chars={}",
                            elapsed,
                            prompt_chars,
                        )
                    return _extract_text(resp.json())

                # --- LB error (502/503/504): workers busy ---
                if resp.status_code in _LB_RETRY_STATUSES:
                    if lb_attempts < len(_LB_BACKOFF_SCHEDULE):
                        delay = _LB_BACKOFF_SCHEDULE[lb_attempts]
                        lb_attempts += 1
                        logger.warning(
                            "Qwen LB error HTTP {} (attempt {}/{}, "
                            "backoff={:.0f}s prompt_chars={}): {!r}",
                            resp.status_code,
                            lb_attempts,
                            len(_LB_BACKOFF_SCHEDULE),
                            delay,
                            prompt_chars,
                            resp.text[:200],
                        )
                        time.sleep(delay)
                        continue

                # --- Context overflow (400) ---
                if resp.status_code == 400 and _looks_like_context_overflow(resp.text):
                    logger.error(
                        "Qwen context overflow HTTP {} prompt_chars={}: {!r}",
                        resp.status_code,
                        prompt_chars,
                        resp.text[:1000],
                    )
                    raise QwenContextOverflowError(
                        status_code=resp.status_code,
                        body=resp.text,
                        prompt_chars=prompt_chars,
                    )

                # --- Other HTTP errors (no retry) ---
                logger.error(
                    "Qwen HTTP {} body={!r} prompt_chars={}",
                    resp.status_code,
                    resp.text[:1000],
                    prompt_chars,
                )
                resp.raise_for_status()

            except httpx.ReadTimeout:
                elapsed = time.monotonic() - t0
                if timeout_attempts < _TIMEOUT_MAX_RETRIES:
                    timeout_attempts += 1
                    logger.warning(
                        "Qwen read timeout after {:.1f}s (attempt {}/{}, "
                        "retry_delay={:.0f}s prompt_chars={})",
                        elapsed,
                        timeout_attempts,
                        _TIMEOUT_MAX_RETRIES,
                        _TIMEOUT_RETRY_DELAY_S,
                        prompt_chars,
                    )
                    time.sleep(_TIMEOUT_RETRY_DELAY_S)
                    continue
                logger.error(
                    "Qwen read timeout after {:.1f}s, retries exhausted "
                    "(prompt_chars={})",
                    elapsed,
                    prompt_chars,
                )
                raise

            except (httpx.ConnectError, httpx.ConnectTimeout):
                elapsed = time.monotonic() - t0
                if conn_attempts < _CONN_ERROR_MAX_RETRIES:
                    conn_attempts += 1
                    logger.warning(
                        "Qwen connection error (attempt {}/{}, "
                        "retry_delay={:.0f}s prompt_chars={})",
                        conn_attempts,
                        _CONN_ERROR_MAX_RETRIES,
                        _CONN_ERROR_RETRY_DELAY_S,
                        prompt_chars,
                    )
                    time.sleep(_CONN_ERROR_RETRY_DELAY_S)
                    continue
                logger.error(
                    "Qwen connection failed after {} retries (prompt_chars={})",
                    conn_attempts,
                    prompt_chars,
                )
                raise

        # Unreachable, but keeps mypy happy.
        raise RuntimeError("Qwen retry loop exited unexpectedly")  # pragma: no cover

    def chat_json(
        self,
        messages: list[dict[str, str]],
        schema: type[T],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        repair_attempts: int = 1,
    ) -> T:
        """Call ``chat`` then validate against ``schema``, with up to one repair.

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
    """Async sibling of :class:`QwenClient` with identical retry semantics."""

    def __init__(self, config: QwenClientConfig | None = None) -> None:
        self.config = config or QwenClientConfig.from_env()
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.config.base_url,
                timeout=self.config.httpx_timeout(),
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
        """Return the raw assistant text, with retry on transient failures."""
        body = _payload(
            self.config,
            messages,
            response_format=response_format,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        prompt_chars = sum(len(m.get("content", "")) for m in messages)

        lb_attempts = 0
        timeout_attempts = 0
        conn_attempts = 0
        total_attempt = 0

        while True:
            total_attempt += 1
            t0 = time.monotonic()
            try:
                resp = await self._http().post("/chat/completions", json=body)
                elapsed = time.monotonic() - t0

                if resp.status_code < 400:
                    if total_attempt > 1:
                        logger.info(
                            "Qwen request succeeded on attempt {} "
                            "(response_time={:.1f}s prompt_chars={})",
                            total_attempt,
                            elapsed,
                            prompt_chars,
                        )
                    else:
                        logger.debug(
                            "Qwen response_time={:.1f}s prompt_chars={}",
                            elapsed,
                            prompt_chars,
                        )
                    return _extract_text(resp.json())

                if resp.status_code in _LB_RETRY_STATUSES:
                    if lb_attempts < len(_LB_BACKOFF_SCHEDULE):
                        delay = _LB_BACKOFF_SCHEDULE[lb_attempts]
                        lb_attempts += 1
                        logger.warning(
                            "Qwen LB error HTTP {} (attempt {}/{}, "
                            "backoff={:.0f}s prompt_chars={}): {!r}",
                            resp.status_code,
                            lb_attempts,
                            len(_LB_BACKOFF_SCHEDULE),
                            delay,
                            prompt_chars,
                            resp.text[:200],
                        )
                        await asyncio.sleep(delay)
                        continue

                if resp.status_code == 400 and _looks_like_context_overflow(resp.text):
                    logger.error(
                        "Qwen context overflow HTTP {} prompt_chars={}: {!r}",
                        resp.status_code,
                        prompt_chars,
                        resp.text[:1000],
                    )
                    raise QwenContextOverflowError(
                        status_code=resp.status_code,
                        body=resp.text,
                        prompt_chars=prompt_chars,
                    )

                logger.error(
                    "Qwen HTTP {} body={!r} prompt_chars={}",
                    resp.status_code,
                    resp.text[:1000],
                    prompt_chars,
                )
                resp.raise_for_status()

            except httpx.ReadTimeout:
                elapsed = time.monotonic() - t0
                if timeout_attempts < _TIMEOUT_MAX_RETRIES:
                    timeout_attempts += 1
                    logger.warning(
                        "Qwen read timeout after {:.1f}s (attempt {}/{}, "
                        "retry_delay={:.0f}s prompt_chars={})",
                        elapsed,
                        timeout_attempts,
                        _TIMEOUT_MAX_RETRIES,
                        _TIMEOUT_RETRY_DELAY_S,
                        prompt_chars,
                    )
                    await asyncio.sleep(_TIMEOUT_RETRY_DELAY_S)
                    continue
                logger.error(
                    "Qwen read timeout after {:.1f}s, retries exhausted "
                    "(prompt_chars={})",
                    elapsed,
                    prompt_chars,
                )
                raise

            except (httpx.ConnectError, httpx.ConnectTimeout):
                elapsed = time.monotonic() - t0
                if conn_attempts < _CONN_ERROR_MAX_RETRIES:
                    conn_attempts += 1
                    logger.warning(
                        "Qwen connection error (attempt {}/{}, "
                        "retry_delay={:.0f}s prompt_chars={})",
                        conn_attempts,
                        _CONN_ERROR_MAX_RETRIES,
                        _CONN_ERROR_RETRY_DELAY_S,
                        prompt_chars,
                    )
                    await asyncio.sleep(_CONN_ERROR_RETRY_DELAY_S)
                    continue
                logger.error(
                    "Qwen connection failed after {} retries (prompt_chars={})",
                    conn_attempts,
                    prompt_chars,
                )
                raise

        raise RuntimeError("Qwen retry loop exited unexpectedly")  # pragma: no cover

    async def chat_json(
        self,
        messages: list[dict[str, str]],
        schema: type[T],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        repair_attempts: int = 1,
    ) -> T:
        text = await self.chat(
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
        try:
            setattr(last_err, "last_raw_text", last_text)
        except Exception:
            pass
        raise last_err

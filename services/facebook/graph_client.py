"""Thin async httpx client for the Facebook Graph API.

Usage::

    async with GraphClient(access_token="...") as client:
        data = await client.get("/me", params={"fields": "id,name"})
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from loguru import logger


_DEFAULT_VERSION = "v22.0"
_BASE = "https://graph.facebook.com"


def _graph_version() -> str:
    return os.environ.get("FACEBOOK_GRAPH_API_VERSION", _DEFAULT_VERSION)


class FacebookAPIError(Exception):
    """Raised when the Graph API returns an error object."""

    def __init__(self, message: str, code: int = 0, subcode: int = 0) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.subcode = subcode

    def is_token_expired(self) -> bool:
        """OAuthException 190 — user token expired / revoked."""
        return self.code == 190

    def is_permission_missing(self) -> bool:
        """Error 200 (permission) or 100 (invalid param / missing scope)."""
        return self.code in (200, 100)


class GraphClient:
    """Async context-manager wrapper around httpx.AsyncClient."""

    def __init__(
        self,
        access_token: str,
        *,
        version: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._token = access_token
        self._version = version or _graph_version()
        self._base_url = f"{_BASE}/{self._version}"
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "GraphClient":
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _check(self, data: dict[str, Any]) -> None:
        """Raise FacebookAPIError if the response contains an error object."""
        err = data.get("error")
        if not err:
            return
        code = int(err.get("code", 0))
        subcode = int(err.get("error_subcode", 0))
        msg = err.get("message", "Unknown Facebook API error")
        raise FacebookAPIError(msg, code=code, subcode=subcode)

    async def get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        assert self._client is not None, "GraphClient must be used as async context manager"
        merged = {"access_token": self._token, **(params or {})}
        resp = await self._client.get(path, params=merged)
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        self._check(data)
        return data

    async def post(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        full_url: str | None = None,
    ) -> dict[str, Any]:
        assert self._client is not None, "GraphClient must be used as async context manager"
        merged_params = {"access_token": self._token, **(params or {})}
        url = full_url or path
        resp = await self._client.post(
            url,
            params=merged_params,
            json=json,
            content=data,
            headers=headers or {},
        )
        resp.raise_for_status()
        body: dict[str, Any] = resp.json()
        self._check(body)
        return body

    async def delete(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """DELETE a Graph API object (e.g. a published post/reel)."""
        assert self._client is not None, "GraphClient must be used as async context manager"
        merged = {"access_token": self._token, **(params or {})}
        resp = await self._client.delete(path, params=merged)
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        self._check(data)
        return data

    async def post_raw(
        self,
        full_url: str,
        *,
        content: bytes,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """POST to an arbitrary URL (e.g. resumable upload endpoint)."""
        assert self._client is not None, "GraphClient must be used as async context manager"
        resp = await self._client.post(
            full_url,
            content=content,
            headers=headers or {},
        )
        resp.raise_for_status()
        body: dict[str, Any] = resp.json()
        self._check(body)
        return body

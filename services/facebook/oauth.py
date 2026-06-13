"""Facebook OAuth 2.0 flow helpers.

Provides URL generation and code-exchange logic. Does NOT touch the database —
callers are responsible for persisting the resulting token.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from loguru import logger


_OAUTH_BASE = "https://www.facebook.com"
_GRAPH_BASE = "https://graph.facebook.com"

_REQUIRED_SCOPES = (
    "pages_show_list,"
    "pages_read_engagement,"
    "pages_manage_posts,"
    "pages_read_user_content,"
    "pages_manage_engagement"
)


def _settings() -> tuple[str, str, str, str]:
    """Return (app_id, app_secret, redirect_uri, graph_version) from env."""
    app_id = os.environ.get("FACEBOOK_APP_ID", "")
    app_secret = os.environ.get("FACEBOOK_APP_SECRET", "")
    redirect_uri = os.environ.get(
        "FACEBOOK_REDIRECT_URI", "http://localhost:8080/auth/facebook/callback"
    )
    version = os.environ.get("FACEBOOK_GRAPH_API_VERSION", "v22.0")
    return app_id, app_secret, redirect_uri, version


def build_login_url(*, state: str | None = None) -> str:
    """Return the Facebook OAuth dialog URL to redirect the user to."""
    app_id, _, redirect_uri, version = _settings()
    params: dict[str, str] = {
        "client_id": app_id,
        "redirect_uri": redirect_uri,
        "scope": _REQUIRED_SCOPES,
        "response_type": "code",
    }
    if state:
        params["state"] = state
    return f"{_OAUTH_BASE}/{version}/dialog/oauth?{urlencode(params)}"


@dataclass
class OAuthTokenResult:
    access_token: str
    token_type: str
    expires_in: int | None  # seconds; None if not returned


async def exchange_code(code: str) -> OAuthTokenResult:
    """Exchange an authorization code for a user access token."""
    app_id, app_secret, redirect_uri, version = _settings()
    url = f"{_GRAPH_BASE}/{version}/oauth/access_token"
    params = {
        "client_id": app_id,
        "client_secret": app_secret,
        "redirect_uri": redirect_uri,
        "code": code,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()

    if "error" in data:
        err = data["error"]
        raise ValueError(
            f"Facebook token exchange failed: {err.get('message', err)}"
        )

    return OAuthTokenResult(
        access_token=data["access_token"],
        token_type=data.get("token_type", "bearer"),
        expires_in=data.get("expires_in"),
    )


@dataclass
class FacebookUserInfo:
    id: str
    name: str
    picture_url: str | None


async def fetch_me(access_token: str) -> FacebookUserInfo:
    """Fetch basic profile info for the authenticated user."""
    app_id, _, _, version = _settings()
    url = f"{_GRAPH_BASE}/{version}/me"
    params = {
        "fields": "id,name,picture.type(large)",
        "access_token": access_token,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()

    if "error" in data:
        err = data["error"]
        raise ValueError(f"Facebook /me failed: {err.get('message', err)}")

    picture_url: str | None = None
    pic = data.get("picture")
    if isinstance(pic, dict):
        picture_url = pic.get("data", {}).get("url")

    return FacebookUserInfo(
        id=data["id"],
        name=data.get("name", ""),
        picture_url=picture_url,
    )

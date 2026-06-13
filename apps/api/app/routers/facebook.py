"""Facebook REST routes.

Mounted at /facebook (accounts, pages) and /auth/facebook (OAuth flow).
"""

from __future__ import annotations

import os
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Query
from fastapi.responses import RedirectResponse
from loguru import logger
from sqlalchemy import select

from app.deps import SessionDep, SettingsDep
from app.errors import NotFoundError, ValidationError
from database.models import FacebookAccount, FacebookPage
from shared_py.crypto import decrypt_token, encrypt_token, mask_token
from shared_py.enums import FacebookAccountStatus, FacebookPageStatus
from shared_py.schemas import (
    FacebookAccountDTO,
    FacebookPageDTO,
    ListFacebookAccountsResponse,
    ListFacebookPagesResponse,
)
from services.facebook.graph_client import FacebookAPIError, GraphClient
from services.facebook.oauth import build_login_url, exchange_code, fetch_me
from services.facebook.page_sync import TokenExpiredError, sync_pages

router = APIRouter(tags=["facebook"])


# ---------------------------------------------------------------------------
# OAuth flow
# ---------------------------------------------------------------------------


@router.get("/auth/facebook/login")
async def facebook_login(settings: SettingsDep) -> RedirectResponse:
    """Redirect the user to the Facebook OAuth dialog."""
    url = build_login_url()
    return RedirectResponse(url=url, status_code=302)


@router.get("/auth/facebook/callback")
async def facebook_callback(
    code: str,
    session: SessionDep,
    settings: SettingsDep,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
) -> dict[str, Any]:
    """Exchange OAuth code for user token, persist account, seed pages."""
    if error:
        raise ValidationError(
            f"Facebook OAuth error: {error_description or error}",
            details={"error": error},
        )

    # 1. Exchange code for user access token
    try:
        token_result = await exchange_code(code)
    except Exception as exc:
        raise ValidationError(f"Token exchange failed: {exc}") from exc

    user_token = token_result.access_token

    # 2. Fetch /me
    try:
        me = await fetch_me(user_token)
    except Exception as exc:
        raise ValidationError(f"Failed to fetch user profile: {exc}") from exc

    logger.info(
        "facebook_callback: user_id={} name={} token={}",
        me.id,
        me.name,
        mask_token(user_token),
    )

    # 3. Upsert FacebookAccount
    result = await session.execute(
        select(FacebookAccount).where(
            FacebookAccount.provider_user_id == me.id
        )
    )
    account: FacebookAccount | None = result.scalar_one_or_none()

    encrypted_token = encrypt_token(user_token)

    if account is None:
        account = FacebookAccount(
            provider_user_id=me.id,
            display_name=me.name,
            avatar_url=me.picture_url,
            encrypted_access_token=encrypted_token,
            status=FacebookAccountStatus.ACTIVE,
        )
        session.add(account)
        await session.flush()
        logger.info("facebook_callback: created account id={}", account.id)
    else:
        account.display_name = me.name
        account.avatar_url = me.picture_url
        account.encrypted_access_token = encrypted_token
        account.status = FacebookAccountStatus.ACTIVE
        await session.flush()
        logger.info("facebook_callback: updated account id={}", account.id)

    await session.commit()

    # 4. Seed pages (best-effort — don't fail the callback if sync errors)
    try:
        pages = await sync_pages(account.id)
        page_count = len(pages)
    except TokenExpiredError:
        page_count = 0
        logger.warning("facebook_callback: token expired during page sync")
    except Exception as exc:
        page_count = 0
        logger.warning("facebook_callback: page sync failed: {}", exc)

    return {
        "account_id": str(account.id),
        "provider_user_id": me.id,
        "display_name": me.name,
        "pages_synced": page_count,
    }


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------


@router.get("/facebook/accounts", response_model=ListFacebookAccountsResponse)
async def list_accounts(session: SessionDep) -> ListFacebookAccountsResponse:
    """List all Facebook accounts (no token fields)."""
    result = await session.execute(select(FacebookAccount))
    accounts = result.scalars().all()
    return ListFacebookAccountsResponse(
        accounts=[FacebookAccountDTO.model_validate(a) for a in accounts],
        total=len(accounts),
    )


@router.post("/facebook/accounts/{account_id}/sync")
async def sync_account_pages(
    account_id: UUID, session: SessionDep
) -> dict[str, Any]:
    """Re-run page sync for an account."""
    result = await session.execute(
        select(FacebookAccount).where(FacebookAccount.id == account_id)
    )
    account = result.scalar_one_or_none()
    if account is None:
        raise NotFoundError(f"Account {account_id} not found")

    try:
        pages = await sync_pages(account_id)
        return {"synced": len(pages), "account_id": str(account_id)}
    except TokenExpiredError as exc:
        raise ValidationError(
            "User token is expired. Please re-authenticate.",
            details={"account_id": str(account_id)},
        ) from exc


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@router.get("/facebook/pages", response_model=ListFacebookPagesResponse)
async def list_pages(
    session: SessionDep,
    account_id: UUID | None = Query(default=None),
    status: FacebookPageStatus | None = Query(default=None),
) -> ListFacebookPagesResponse:
    """List pages, optionally filtered by account_id and/or status."""
    stmt = select(FacebookPage)
    if account_id is not None:
        stmt = stmt.where(FacebookPage.account_id == account_id)
    if status is not None:
        stmt = stmt.where(FacebookPage.status == status)
    result = await session.execute(stmt)
    pages = result.scalars().all()
    return ListFacebookPagesResponse(
        pages=[FacebookPageDTO.model_validate(p) for p in pages],
        total=len(pages),
    )


# ---------------------------------------------------------------------------
# Manual page registration (per-page token, no OAuth flow needed)
# ---------------------------------------------------------------------------


_MANUAL_ACCOUNT_PROVIDER_ID = "manual"


async def _get_or_create_manual_account(session: SessionDep) -> FacebookAccount:
    """Single shared 'manual' account container for token-only page entries."""
    result = await session.execute(
        select(FacebookAccount).where(
            FacebookAccount.provider_user_id == _MANUAL_ACCOUNT_PROVIDER_ID
        )
    )
    account = result.scalar_one_or_none()
    if account is None:
        account = FacebookAccount(
            provider_user_id=_MANUAL_ACCOUNT_PROVIDER_ID,
            display_name="Manual tokens",
            avatar_url=None,
            encrypted_access_token=encrypt_token(""),
            token_expires_at=None,
            status=FacebookAccountStatus.ACTIVE,
        )
        session.add(account)
        await session.flush()
    return account


@router.post("/facebook/pages", response_model=FacebookPageDTO)
async def create_page_manual(
    body: dict[str, Any],
    session: SessionDep,
) -> FacebookPageDTO:
    """Register a single page using a long-lived Page Access Token + niche.

    No Facebook app / OAuth flow needed. Body:
      - page_access_token (required) — long-lived page token
      - niche (required) — keyword line for content discovery
      - language (optional) — "vi" or "en", defaults to "vi"
      - content_keywords (optional) — list[str]
      - blocked_keywords (optional) — list[str]
      - daily_reel_target (optional) — int 1-20, default 3
      - posting_time_slots (optional) — list of {day_of_week, hour, minute}
      - auto_generate_enabled (optional) — bool, default false

    The token is verified against /me?fields=id,name,picture using a Page
    token (which acts on the page itself, returning the page identity). On
    success the page is upserted by ``page_id`` and the token is encrypted.
    """
    token = (body.get("page_access_token") or "").strip()
    niche = (body.get("niche") or "").strip()
    if not token:
        raise ValidationError("page_access_token is required")
    if not niche:
        raise ValidationError("niche is required")

    language = (body.get("language") or "vi").lower()
    if language not in ("vi", "en"):
        raise ValidationError("language must be 'vi' or 'en'")

    # 1. Verify the token. A page access token responds to /me with the page itself.
    try:
        async with GraphClient(access_token=token) as client:
            me = await client.get(
                "/me", params={"fields": "id,name,picture.type(large)"}
            )
    except FacebookAPIError as exc:
        if exc.is_token_expired():
            raise ValidationError(
                "Facebook token is invalid or expired",
                details={"code": exc.code},
            ) from exc
        raise ValidationError(
            f"Facebook token verification failed: {exc.message}",
            details={"code": exc.code},
        ) from exc
    except Exception as exc:
        raise ValidationError(
            f"Could not reach Facebook API: {exc}"
        ) from exc

    page_id_str = str(me.get("id", "")).strip()
    page_name = me.get("name") or "Unnamed page"
    picture = me.get("picture") or {}
    avatar_url = (picture.get("data") or {}).get("url") or ""

    if not page_id_str:
        raise ValidationError(
            "Token did not resolve to a Facebook page (missing id)"
        )

    # 2. Get-or-create the manual account container.
    account = await _get_or_create_manual_account(session)

    # 3. Upsert by page_id. If exists, refresh token + niche.
    existing_q = await session.execute(
        select(FacebookPage).where(FacebookPage.page_id == page_id_str)
    )
    existing = existing_q.scalar_one_or_none()

    daily_target = int(body.get("daily_reel_target") or 3)
    if daily_target < 1 or daily_target > 20:
        raise ValidationError("daily_reel_target must be between 1 and 20")

    page_fields: dict[str, Any] = {
        "encrypted_page_access_token": encrypt_token(token),
        "page_name": page_name,
        "avatar_url": avatar_url or "",
        "niche": niche,
        "language": language,
        "content_keywords": list(body.get("content_keywords") or []),
        "blocked_keywords": list(body.get("blocked_keywords") or []),
        "daily_reel_target": daily_target,
        "posting_time_slots": list(body.get("posting_time_slots") or []),
        "auto_generate_enabled": bool(body.get("auto_generate_enabled", False)),
        "require_manual_approval": True,
        "status": FacebookPageStatus.ACTIVE,
    }

    if existing is not None:
        for k, v in page_fields.items():
            setattr(existing, k, v)
        page = existing
    else:
        page = FacebookPage(
            account_id=account.id,
            page_id=page_id_str,
            **page_fields,
        )
        session.add(page)

    await session.commit()
    await session.refresh(page)
    logger.info(
        "facebook: manual page registered page_id={} name={!r} niche={!r} token={}",
        page_id_str,
        page_name,
        niche,
        mask_token(token),
    )
    return FacebookPageDTO.model_validate(page)


@router.delete("/facebook/pages/{page_id}")
async def delete_page(page_id: UUID, session: SessionDep) -> dict[str, Any]:
    """Permanently remove a page (and its content_sources / reel_drafts via FK CASCADE)."""
    result = await session.execute(
        select(FacebookPage).where(FacebookPage.id == page_id)
    )
    page = result.scalar_one_or_none()
    if page is None:
        raise NotFoundError(f"Page {page_id} not found")
    page_id_str = page.page_id
    await session.delete(page)
    await session.commit()
    return {"deleted": True, "page_id": str(page_id), "facebook_page_id": page_id_str}


@router.get("/facebook/pages/{page_id}", response_model=FacebookPageDTO)
async def get_page(page_id: UUID, session: SessionDep) -> FacebookPageDTO:
    """Get a single page with full config (no encrypted token fields)."""
    result = await session.execute(
        select(FacebookPage).where(FacebookPage.id == page_id)
    )
    page = result.scalar_one_or_none()
    if page is None:
        raise NotFoundError(f"Page {page_id} not found")
    return FacebookPageDTO.model_validate(page)


@router.patch("/facebook/pages/{page_id}", response_model=FacebookPageDTO)
async def update_page(
    page_id: UUID,
    body: dict[str, Any],
    session: SessionDep,
) -> FacebookPageDTO:
    """Update editable page fields.

    Allowed: niche, content_keywords, blocked_keywords, daily_reel_target,
    posting_time_slots, auto_generate_enabled.
    Forbidden: page_id, encrypted_*, status (use dedicated endpoints).
    """
    _FORBIDDEN = {
        "page_id", "encrypted_page_access_token", "status",
        "account_id", "id", "created_at", "updated_at",
    }
    _ALLOWED = {
        "niche", "content_keywords", "blocked_keywords",
        "daily_reel_target", "posting_time_slots", "auto_generate_enabled",
        "page_name", "language", "require_manual_approval",
    }

    result = await session.execute(
        select(FacebookPage).where(FacebookPage.id == page_id)
    )
    page = result.scalar_one_or_none()
    if page is None:
        raise NotFoundError(f"Page {page_id} not found")

    for key, value in body.items():
        if key in _FORBIDDEN:
            raise ValidationError(
                f"Field '{key}' cannot be updated via this endpoint",
                details={"field": key},
            )
        if key in _ALLOWED:
            setattr(page, key, value)

    await session.commit()
    return FacebookPageDTO.model_validate(page)


@router.post("/facebook/pages/{page_id}/disable")
async def disable_page(page_id: UUID, session: SessionDep) -> dict[str, Any]:
    """Disable a page (stop auto-generation and publishing)."""
    result = await session.execute(
        select(FacebookPage).where(FacebookPage.id == page_id)
    )
    page = result.scalar_one_or_none()
    if page is None:
        raise NotFoundError(f"Page {page_id} not found")
    page.status = FacebookPageStatus.DISABLED
    await session.commit()
    return {"page_id": str(page_id), "status": FacebookPageStatus.DISABLED}


@router.post("/facebook/pages/{page_id}/enable")
async def enable_page(page_id: UUID, session: SessionDep) -> dict[str, Any]:
    """Re-enable a previously disabled page."""
    result = await session.execute(
        select(FacebookPage).where(FacebookPage.id == page_id)
    )
    page = result.scalar_one_or_none()
    if page is None:
        raise NotFoundError(f"Page {page_id} not found")
    page.status = FacebookPageStatus.ACTIVE
    await session.commit()
    return {"page_id": str(page_id), "status": FacebookPageStatus.ACTIVE}


@router.post("/facebook/pages/{page_id}/test-token")
async def test_page_token(page_id: UUID, session: SessionDep) -> dict[str, Any]:
    """Verify the stored page access token by calling /{page_id}?fields=id.

    Sets status=active on success, status=token_expired on 401/190.
    """
    result = await session.execute(
        select(FacebookPage).where(FacebookPage.id == page_id)
    )
    page = result.scalar_one_or_none()
    if page is None:
        raise NotFoundError(f"Page {page_id} not found")

    try:
        page_token = decrypt_token(page.encrypted_page_access_token)
    except Exception as exc:
        raise ValidationError(f"Could not decrypt page token: {exc}") from exc

    try:
        async with GraphClient(page_token) as client:
            await client.get(f"/{page.page_id}", params={"fields": "id"})
        page.status = FacebookPageStatus.ACTIVE
        await session.commit()
        return {"page_id": str(page_id), "status": FacebookPageStatus.ACTIVE, "ok": True}
    except FacebookAPIError as exc:
        if exc.is_token_expired():
            page.status = FacebookPageStatus.TOKEN_EXPIRED
        elif exc.is_permission_missing():
            page.status = FacebookPageStatus.PERMISSION_MISSING
        else:
            page.status = FacebookPageStatus.ERROR
        await session.commit()
        return {
            "page_id": str(page_id),
            "status": page.status,
            "ok": False,
            "error": exc.message,
        }
    except Exception as exc:
        page.status = FacebookPageStatus.ERROR
        await session.commit()
        return {
            "page_id": str(page_id),
            "status": FacebookPageStatus.ERROR,
            "ok": False,
            "error": str(exc),
        }

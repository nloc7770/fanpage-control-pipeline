"""Page sync: fetch /me/accounts and upsert FacebookAccount + FacebookPage rows.

Public API::

    pages = await sync_pages(account_id)

Raises :class:`TokenExpiredError` when the stored user token is expired/revoked.
Per-page errors are logged and that page's status is set to ``error``; other
pages continue to be processed.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any
from uuid import UUID

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database.models import FacebookAccount, FacebookPage
from shared_py.crypto import decrypt_token, encrypt_token, mask_token
from shared_py.enums import FacebookAccountStatus, FacebookPageStatus
from services.facebook.graph_client import FacebookAPIError, GraphClient


class TokenExpiredError(Exception):
    """Raised when the stored user access token is expired or revoked."""


def _database_url() -> str:
    return os.environ.get(
        "DATABASE_URL", "postgresql+asyncpg://factory:factory@postgres:5432/factory"
    )


async def _get_session_factory() -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(
        _database_url(),
        future=True,
        pool_size=1,
        max_overflow=0,
        pool_pre_ping=True,
    )
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def sync_pages(account_id: UUID) -> list[FacebookPage]:
    """Fetch /me/accounts and upsert pages for the given account.

    Returns the list of upserted :class:`FacebookPage` ORM objects.
    Raises :class:`TokenExpiredError` if the user token is expired.
    """
    factory = await _get_session_factory()
    async with factory() as session:
        try:
            result = await session.execute(
                select(FacebookAccount).where(FacebookAccount.id == account_id)
            )
            account: FacebookAccount | None = result.scalar_one_or_none()
            if account is None:
                raise ValueError(f"FacebookAccount {account_id} not found")

            user_token = decrypt_token(account.encrypted_access_token)
            logger.debug(
                "sync_pages: account={} token={}", account_id, mask_token(user_token)
            )

            try:
                pages_data = await _fetch_me_accounts(user_token)
            except FacebookAPIError as exc:
                if exc.is_token_expired():
                    logger.warning(
                        "sync_pages: token expired for account={}", account_id
                    )
                    account.status = FacebookAccountStatus.TOKEN_EXPIRED
                    await session.commit()
                    raise TokenExpiredError(
                        f"User token expired for account {account_id}"
                    ) from exc
                raise

            upserted: list[FacebookPage] = []
            for page_data in pages_data:
                try:
                    page = await _upsert_page(session, account, page_data)
                    upserted.append(page)
                except Exception as exc:
                    page_id_str = page_data.get("id", "unknown")
                    logger.error(
                        "sync_pages: error upserting page={} account={}: {}",
                        page_id_str,
                        account_id,
                        exc,
                    )
                    # Mark that page as error if it already exists
                    await _mark_page_error(session, page_id_str)

            await session.commit()
            logger.info(
                "sync_pages: account={} synced {} pages", account_id, len(upserted)
            )
            return upserted

        except TokenExpiredError:
            raise
        except Exception:
            await session.rollback()
            raise


async def _fetch_me_accounts(user_token: str) -> list[dict[str, Any]]:
    """Call GET /me/accounts and return the list of page dicts."""
    async with GraphClient(user_token) as client:
        data = await client.get(
            "/me/accounts",
            params={"fields": "id,name,access_token,picture,perms"},
        )
    return data.get("data", [])


async def _upsert_page(
    session: AsyncSession,
    account: FacebookAccount,
    page_data: dict[str, Any],
) -> FacebookPage:
    """Insert or update a FacebookPage row from Graph API page data."""
    page_id_str: str = page_data["id"]
    page_name: str = page_data.get("name", "")
    raw_token: str = page_data.get("access_token", "")
    perms: list[str] = page_data.get("perms", [])

    # Extract avatar URL from picture object
    avatar_url = ""
    pic = page_data.get("picture")
    if isinstance(pic, dict):
        avatar_url = pic.get("data", {}).get("url", "")

    encrypted_token = encrypt_token(raw_token) if raw_token else ""

    result = await session.execute(
        select(FacebookPage).where(FacebookPage.page_id == page_id_str)
    )
    existing: FacebookPage | None = result.scalar_one_or_none()

    if existing is not None:
        existing.page_name = page_name
        existing.avatar_url = avatar_url
        if raw_token:
            existing.encrypted_page_access_token = encrypted_token
        existing.permissions = {"perms": perms}
        # Only restore to active if it was in an error/expired state
        if existing.status in (
            FacebookPageStatus.TOKEN_EXPIRED,
            FacebookPageStatus.ERROR,
        ):
            existing.status = FacebookPageStatus.ACTIVE
        await session.flush()
        return existing
    else:
        page = FacebookPage(
            account_id=account.id,
            page_id=page_id_str,
            page_name=page_name,
            avatar_url=avatar_url,
            encrypted_page_access_token=encrypted_token,
            permissions={"perms": perms},
            status=FacebookPageStatus.ACTIVE,
        )
        session.add(page)
        await session.flush()
        return page


async def _mark_page_error(session: AsyncSession, page_id_str: str) -> None:
    """Best-effort: set status=error on an existing page row."""
    try:
        result = await session.execute(
            select(FacebookPage).where(FacebookPage.page_id == page_id_str)
        )
        page = result.scalar_one_or_none()
        if page is not None:
            page.status = FacebookPageStatus.ERROR
            await session.flush()
    except Exception as exc:
        logger.warning("_mark_page_error: failed for page_id={}: {}", page_id_str, exc)

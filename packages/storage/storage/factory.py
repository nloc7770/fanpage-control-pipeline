"""Factory: pick the backend from environment."""

from __future__ import annotations

import os
from functools import lru_cache

from loguru import logger

from storage.base import StorageBackend
from storage.local import LocalStorage
from storage.s3 import S3Storage


@lru_cache(maxsize=1)
def get_storage() -> StorageBackend:
    """Return the storage backend selected by ``STORAGE_BACKEND``.

    Cached so the entire process reuses the same instance (and, for S3, the
    same boto3 client). Pass-through for tests by calling ``get_storage.cache_clear()``.
    """
    backend = os.environ.get("STORAGE_BACKEND", "local").strip().lower()
    if backend == "s3":
        logger.info("storage backend = S3")
        return S3Storage.from_env()
    if backend != "local":
        logger.warning("unknown STORAGE_BACKEND={!r}; falling back to local", backend)
    root = os.environ.get("STORAGE_LOCAL_PATH", "/data/storage")
    logger.info("storage backend = Local (root={})", root)
    return LocalStorage(root)

"""Storage backend abstraction.

A single :class:`StorageBackend` ABC with two implementations: :class:`LocalStorage`
(writes under ``STORAGE_LOCAL_PATH``) and :class:`S3Storage` (boto3 -- skeleton
only until S3 creds are supplied). :func:`get_storage` reads ``STORAGE_BACKEND``
from the environment and returns the right one.
"""

from __future__ import annotations

from storage.base import StorageBackend, StoragePutResult
from storage.factory import get_storage
from storage.local import LocalStorage
from storage.s3 import S3Storage

__all__ = [
    "LocalStorage",
    "S3Storage",
    "StorageBackend",
    "StoragePutResult",
    "get_storage",
]

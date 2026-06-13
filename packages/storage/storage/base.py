"""Storage backend interface.

Keys are POSIX-style strings such as ``{job_id}/{kind}/{filename}``. Backends
must accept and emit them verbatim; never URL-encode or normalize. The return
value of :meth:`put` is the canonical ``path`` stored in the ``assets.path``
column.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


@dataclass(frozen=True, slots=True)
class StoragePutResult:
    """Return shape from :meth:`StorageBackend.put`.

    Attributes
    ----------
    key:
        The storage key, exactly as passed in.
    path:
        Backend-specific canonical path stored in ``assets.path`` (filesystem
        path for local, ``s3://bucket/key`` for S3).
    size_bytes:
        Final size on disk / object after upload.
    """

    key: str
    path: str
    size_bytes: int


class StorageBackend(ABC):
    """Abstract storage backend. Sync interface -- workers wrap with ``run_in_executor``."""

    @abstractmethod
    def put(self, key: str, src_path: Path) -> StoragePutResult:
        """Persist ``src_path`` at ``key``. Overwrites if it already exists."""

    @abstractmethod
    def get_path(self, key: str) -> Path:
        """Return a local filesystem path for ``key``.

        For local storage this is the backing file. For S3 this raises
        :class:`NotImplementedError` -- callers should use :meth:`open_stream`
        or download via a presigned URL instead.
        """

    @abstractmethod
    def open_stream(self, key: str) -> Iterator[bytes]:
        """Yield the object's bytes in chunks (for streaming downloads)."""

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Return True if the object exists."""

    @abstractmethod
    def url_for(self, key: str, expires_s: int = 3600) -> str:
        """Return a URL that can be used to fetch the object.

        Local backend returns an in-app path (the API serves it); S3 returns a
        presigned HTTPS URL.
        """

    # Convenience: not abstract because most backends share an implementation.
    def put_bytes(self, key: str, data: bytes) -> StoragePutResult:
        """Write ``data`` at ``key`` by spilling to a temp file first."""
        import tempfile

        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(data)
            tmp_path = Path(tmp.name)
        try:
            return self.put(key, tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)

    def put_fileobj(self, key: str, fileobj: BinaryIO) -> StoragePutResult:
        """Drain ``fileobj`` to a temp file and call :meth:`put`."""
        import tempfile

        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            while True:
                chunk = fileobj.read(1 << 20)
                if not chunk:
                    break
                tmp.write(chunk)
            tmp_path = Path(tmp.name)
        try:
            return self.put(key, tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)

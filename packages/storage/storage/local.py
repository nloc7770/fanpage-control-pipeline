"""Local-filesystem storage backend.

Files are written under ``{root}/{key}``. ``url_for`` returns an internal
``/assets/...`` path that the API maps to a streaming download endpoint.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

from loguru import logger

from storage.base import StorageBackend, StoragePutResult

CHUNK_BYTES = 1 << 20  # 1 MiB


class LocalStorage(StorageBackend):
    """Persist assets under ``root_path``."""

    def __init__(self, root_path: str | Path) -> None:
        self.root = Path(root_path).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    # ---- internal helpers -----------------------------------------------

    def _resolve(self, key: str) -> Path:
        """Resolve ``key`` to an absolute path inside :attr:`root` (traversal-safe)."""
        if not key:
            raise ValueError("storage key must be non-empty")
        # Strip any leading slashes so Path treats it as relative.
        rel = key.lstrip("/")
        target = (self.root / rel).resolve()
        # Defense-in-depth: refuse paths that escape the root.
        if not str(target).startswith(str(self.root)):
            raise ValueError(f"storage key escapes root: {key}")
        return target

    # ---- StorageBackend --------------------------------------------------

    def put(self, key: str, src_path: Path) -> StoragePutResult:
        target = self._resolve(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Use copy2 to preserve mtime; cross-device safe (handles tmp -> data).
        shutil.copy2(str(src_path), str(target))
        size = target.stat().st_size
        logger.debug("LocalStorage.put key={} bytes={}", key, size)
        return StoragePutResult(key=key, path=str(target), size_bytes=size)

    def get_path(self, key: str) -> Path:
        target = self._resolve(key)
        if not target.exists():
            raise FileNotFoundError(f"storage key not found: {key}")
        return target

    def open_stream(self, key: str) -> Iterator[bytes]:
        path = self.get_path(key)
        with path.open("rb") as fp:
            while True:
                chunk = fp.read(CHUNK_BYTES)
                if not chunk:
                    return
                yield chunk

    def exists(self, key: str) -> bool:
        try:
            return self._resolve(key).exists()
        except ValueError:
            return False

    def url_for(self, key: str, expires_s: int = 3600) -> str:
        # The API renders this as ``/assets/{asset_id}/download`` when it
        # writes the assets row; here we return a stable in-app reference.
        del expires_s
        return f"local://{key}"

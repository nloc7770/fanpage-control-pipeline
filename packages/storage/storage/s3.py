"""S3-compatible storage backend (boto3, optional).

Skeleton implementation. ``boto3`` is an optional dependency
(``pip install storage[s3]``); when missing the constructor raises so that
:func:`storage.get_storage` falls back to local.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from loguru import logger

from storage.base import StorageBackend, StoragePutResult

try:  # pragma: no cover - import-time toggle
    import boto3  # type: ignore[import-untyped]
    from botocore.exceptions import ClientError  # type: ignore[import-untyped]

    _BOTO3_AVAILABLE = True
except Exception:  # ImportError or any partial install
    boto3 = None  # type: ignore[assignment]
    ClientError = Exception  # type: ignore[assignment,misc]
    _BOTO3_AVAILABLE = False


class S3Storage(StorageBackend):
    """boto3-backed S3 storage.

    Accepts any S3-compatible endpoint (MinIO, R2, ...). When boto3 isn't
    installed or required credentials are missing, all I/O methods raise
    :class:`NotImplementedError` -- the interface stays complete so callers
    can wire the backend even in environments where it won't be used.
    """

    def __init__(
        self,
        bucket: str,
        *,
        endpoint_url: str | None = None,
        region: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        force_path_style: bool = True,
    ) -> None:
        self.bucket = bucket
        self.endpoint_url = endpoint_url
        self.region = region
        self._creds = {
            "aws_access_key_id": access_key_id,
            "aws_secret_access_key": secret_access_key,
            "region_name": region,
            "endpoint_url": endpoint_url,
        }
        self._force_path_style = force_path_style
        self._client: Any | None = None

        if not _BOTO3_AVAILABLE:
            logger.warning(
                "S3Storage constructed without boto3 installed; calls will raise"
            )

    # ---- internals -------------------------------------------------------

    def _ensure_client(self) -> Any:
        if not _BOTO3_AVAILABLE:
            raise NotImplementedError(
                "S3Storage requires boto3. Install with: pip install storage[s3]"
            )
        if self._client is None:
            config: dict[str, Any] = {}
            if self._force_path_style:
                from botocore.client import Config  # type: ignore[import-untyped]

                config = {"config": Config(s3={"addressing_style": "path"})}
            kwargs = {k: v for k, v in self._creds.items() if v}
            self._client = boto3.client("s3", **kwargs, **config)  # type: ignore[union-attr]
        return self._client

    # ---- StorageBackend --------------------------------------------------

    def put(self, key: str, src_path: Path) -> StoragePutResult:
        client = self._ensure_client()
        client.upload_file(str(src_path), self.bucket, key)
        size = src_path.stat().st_size
        return StoragePutResult(
            key=key, path=f"s3://{self.bucket}/{key}", size_bytes=size
        )

    def get_path(self, key: str) -> Path:
        raise NotImplementedError(
            "S3 backend has no local path; use open_stream() or url_for()"
        )

    def open_stream(self, key: str) -> Iterator[bytes]:
        client = self._ensure_client()
        obj = client.get_object(Bucket=self.bucket, Key=key)
        body = obj["Body"]
        try:
            while True:
                chunk = body.read(1 << 20)
                if not chunk:
                    return
                yield chunk
        finally:
            body.close()

    def exists(self, key: str) -> bool:
        client = self._ensure_client()
        try:
            client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")  # type: ignore[union-attr]
            if code in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def url_for(self, key: str, expires_s: int = 3600) -> str:
        client = self._ensure_client()
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_s,
        )

    @classmethod
    def from_env(cls) -> S3Storage:
        bucket = os.environ.get("S3_BUCKET")
        if not bucket:
            raise RuntimeError("S3_BUCKET env var is required for S3Storage")
        return cls(
            bucket=bucket,
            endpoint_url=os.environ.get("S3_ENDPOINT_URL") or None,
            region=os.environ.get("S3_REGION") or None,
            access_key_id=os.environ.get("S3_ACCESS_KEY_ID") or None,
            secret_access_key=os.environ.get("S3_SECRET_ACCESS_KEY") or None,
            force_path_style=os.environ.get("S3_FORCE_PATH_STYLE", "1") == "1",
        )

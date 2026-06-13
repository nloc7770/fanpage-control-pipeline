# packages/storage

**Status:** placeholder. Owner: Phase-2 workers agent.

Storage abstraction backing the `assets` table.

## Interface

```python
class StorageBackend(Protocol):
    async def put(self, key: str, data: bytes | BinaryIO, *, mime: str | None = None) -> Asset: ...
    async def put_file(self, key: str, src_path: Path, *, mime: str | None = None) -> Asset: ...
    async def get(self, key: str) -> bytes: ...
    async def open(self, key: str) -> AsyncIterator[bytes]: ...  # streaming
    async def url(self, key: str, expires_s: int = 3600) -> str: ...
    async def delete(self, key: str) -> None: ...
    async def exists(self, key: str) -> bool: ...
```

## Implementations

- `LocalStorage(root=STORAGE_LOCAL_PATH)` -- writes to disk under
  `{root}/{job_id}/{kind}/{filename}`. `url()` returns
  `/assets/{asset_id}/download` (handled by the API).
- `S3Storage(bucket, endpoint_url, region, ...)` -- boto3 async via
  `aioboto3`. `url()` returns a presigned URL.

A `make_storage(settings) -> StorageBackend` factory dispatches on
`STORAGE_BACKEND`. The factory also writes a row into `assets` so callers get
a fully populated `AssetDTO` back.

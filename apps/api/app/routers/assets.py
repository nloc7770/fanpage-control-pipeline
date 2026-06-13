"""Assets router: detail and download endpoints."""

from __future__ import annotations

import os
from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.config import get_settings
from app.deps import SessionDep
from app.errors import NotFoundError
from app.services import job_service
from shared_py.schemas import AssetDTO

router = APIRouter(prefix="/assets", tags=["assets"])


def _resolve_storage_path(path: str) -> str:
    """Map a stored asset path (relative or absolute) to a local FS path.

    Local storage stores objects under `STORAGE_LOCAL_PATH/{job_id}/{kind}/...`.
    `Asset.path` may be either absolute or a key relative to the storage root.
    """
    if os.path.isabs(path):
        return path
    root = get_settings().STORAGE_LOCAL_PATH
    return os.path.join(root, path)


@router.get("/{asset_id}", response_model=AssetDTO)
async def get_asset(asset_id: UUID, session: SessionDep) -> AssetDTO:
    asset = await job_service.get_asset(session, asset_id)
    return AssetDTO.model_validate(asset)


@router.get("/{asset_id}/download")
async def download_asset(asset_id: UUID, session: SessionDep) -> FileResponse:
    asset = await job_service.get_asset(session, asset_id)
    fs_path = _resolve_storage_path(asset.path)

    if not os.path.exists(fs_path) or not os.path.isfile(fs_path):
        raise NotFoundError(
            "Asset file not found on storage",
            details={"asset_id": str(asset_id), "path": asset.path},
        )

    filename = os.path.basename(asset.path) or f"{asset.id}"
    media_type = asset.mime or "application/octet-stream"
    # Inline-display media so <video>/<img> tags render in-browser; everything
    # else still triggers a download. Range requests are handled by FileResponse.
    inline = (media_type.startswith(("video/", "image/", "audio/")))
    disposition = "inline" if inline else "attachment"
    return FileResponse(
        path=fs_path,
        media_type=media_type,
        filename=filename,
        headers={"Content-Disposition": f'{disposition}; filename="{filename}"'},
    )

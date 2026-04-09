from __future__ import annotations
import tempfile
from pathlib import Path
from zotero_mcp._app import mcp
from fastmcp import Context
from zotero_mcp.acquisition.resolver import resolve_access
from zotero_mcp.acquisition.config import load_acquisition_config
from zotero_mcp.acquisition.bridge_client import BridgeClient, BridgeDownloadRequest
from zotero_mcp.acquisition.download import ArtifactDownloader
from zotero_mcp.acquisition.types import PipelineError


@mcp.tool()
async def acquire_paper(
    identifier: str,
    session_name: str | None = None,
    ctx: Context = None,
) -> dict:
    config = load_acquisition_config()
    resolution = await resolve_access(identifier, config)

    if not resolution or not resolution.best_location:
        return {"status": "failed", "message": f"Could not resolve: {identifier}"}

    location = resolution.best_location

    if location.requires_session and session_name:
        bridge = BridgeClient()
        if bridge.is_available():
            result = bridge.download(
                BridgeDownloadRequest(
                    doi=identifier,
                    candidate_url=location.url,
                    session_name=session_name,
                )
            )
            if result.status == "complete" and result.file_path:
                return {
                    "status": "complete",
                    "file_path": result.file_path,
                    "provenance": {
                        "bridge_session": session_name,
                        "access_source": "institutional",
                    },
                    "message": f"Downloaded via bridge session '{session_name}'",
                }

    downloader = ArtifactDownloader(config)
    dest_dir = Path(tempfile.mkdtemp(prefix="zotero-mcp-acquire-"))
    download_result = await downloader.download(location.url, dest_dir=dest_dir)

    if isinstance(download_result, PipelineError):
        return {"status": "failed", "message": f"[{download_result.code}] {download_result.message}"}

    return {
        "status": "complete",
        "file_path": str(download_result.file_path),
        "provenance": {"access_source": location.access_method or "url"},
        "message": "Downloaded via HTTP",
    }

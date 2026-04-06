"""download_paper_artifact MCP tool — thin wrapper around acquisition.download."""

from __future__ import annotations
from zotero_mcp._app import mcp
from fastmcp import Context


@mcp.tool()
async def download_paper_artifact(
    url: str,
    expected_type: str = "application/pdf",
    ctx: Context = None,
) -> str:
    """Download a paper artifact from a URL, validating content type and integrity.

    Returns a formatted string with file path, size, and validation status.
    """
    from zotero_mcp.acquisition.download import ArtifactDownloader
    from zotero_mcp.acquisition.config import load_acquisition_config
    from zotero_mcp.acquisition.types import PipelineError
    import tempfile
    from pathlib import Path

    config = load_acquisition_config()
    downloader = ArtifactDownloader(config)
    dest_dir = Path(tempfile.mkdtemp(prefix="zotero-mcp-dl-"))

    result = await downloader.download(url, expected_type=expected_type, dest_dir=dest_dir)

    if isinstance(result, PipelineError):
        return f"Download failed: [{result.code}] {result.message}"

    import re

    lines = [
        f"Downloaded: {result.file_path}",
        f"Content type: {result.content_type}",
        f"Size: {result.size_bytes:,} bytes",
        "Status: validated ✓",
    ]

    lines.append("")
    lines.append("[Paper Ingest Provenance - download step]")
    lines.append("artifact_type: pdf")
    proxy_match = re.search(r"\.([^.]+\.edu|[^.]+\.ac\.[a-z]+)", url)
    if proxy_match:
        lines.append(f"proxy_provider: {proxy_match.group(0)}")

    return "\n".join(lines)

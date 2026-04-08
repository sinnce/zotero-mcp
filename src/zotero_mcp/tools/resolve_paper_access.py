from __future__ import annotations
import asyncio
from zotero_mcp._app import mcp
from fastmcp import Context


@mcp.tool()
async def resolve_paper_access(identifier: str, ctx: Context) -> str:
    """Resolve a paper identifier (DOI, arXiv ID, URL) to open-access PDF locations.

    Returns a formatted string with all resolved access locations and metadata.
    """
    from zotero_mcp.acquisition.resolver import resolve_access
    from zotero_mcp.acquisition.config import load_acquisition_config

    config = load_acquisition_config()
    result = await resolve_access(identifier, config)

    lines = [f"Identifier: {result.identifier_type}:{result.identifier_value}"]

    if result.metadata.get("error"):
        return f"Error: {result.metadata['error']}"

    if result.metadata.get("title"):
        lines.append(f"Title: {result.metadata['title']}")

    lines.append(f"Locations found: {len(result.locations)}")

    if result.best_location:
        lines.append(f"Best URL: {result.best_location.url}")
        lines.append(f"Access method: {result.best_location.access_method}")
        if result.best_location.license:
            lines.append(f"License: {result.best_location.license}")
    else:
        lines.append("No open-access location found.")

    lines.append("")
    lines.append("[Paper Ingest Provenance - resolve step]")
    lines.append(f"access_source: {result.identifier_type}")
    if result.best_location:
        resolver_names = {
            "doi": "Unpaywall OA",
            "arxiv": "arXiv API",
            "url": "Direct URL",
        }
        resolver_name = resolver_names.get(result.identifier_type)
        if resolver_name:
            lines.append(f"resolver_name: {resolver_name}")
        if result.best_location.license:
            lines.append(f"license: {result.best_location.license}")

    return "\n".join(lines)

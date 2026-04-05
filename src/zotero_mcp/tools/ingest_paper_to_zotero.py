from __future__ import annotations
from pathlib import Path
from zotero_mcp._app import mcp
from zotero_mcp.acquisition.ingest import ingest_paper
from zotero_mcp.acquisition.types import PipelineError, ProvenanceMetadata
from zotero_mcp.tools._helpers import _get_write_client
from fastmcp import Context


@mcp.tool()
def ingest_paper_to_zotero(
    title: str,
    file_path: str | None = None,
    doi: str | None = None,
    arxiv_id: str | None = None,
    authors: str | None = None,
    journal: str | None = None,
    year: str | None = None,
    abstract: str | None = None,
    provenance: str | None = None,
    ctx: Context = None,
) -> str:
    """Ingest a paper into Zotero library with optional file attachment.

    Returns item key and status. Handles duplicates gracefully.
    """
    try:
        read_zot, write_zot = _get_write_client(ctx)
    except ValueError as e:
        return f"Error: Cannot write to Zotero — {e}. Set ZOTERO_API_KEY to enable write operations."
    except Exception as e:
        return f"Error connecting to Zotero: {e}"

    prov = ProvenanceMetadata()

    result = ingest_paper(
        write_zot=write_zot,
        read_zot=read_zot,
        title=title,
        doi=doi,
        arxiv_id=arxiv_id,
        authors=authors,
        journal=journal,
        year=year,
        abstract=abstract,
        file_path=Path(file_path) if file_path else None,
        provenance=prov,
    )

    if isinstance(result, PipelineError):
        return f"Ingest failed: [{result.code}] {result.message}"

    if result.provenance and result.provenance.access_source == "duplicate_detected":
        return f"Duplicate detected — existing item key: {result.item_key}"

    lines = [f"Ingested: item key = {result.item_key}"]
    if result.attachment_key:
        lines.append(f"Attachment key: {result.attachment_key}")
    else:
        lines.append("No attachment (no file provided or attachment failed)")
    return "\n".join(lines)

"""extract_paper_content MCP tool — thin wrapper around extraction registry."""

from __future__ import annotations
from pathlib import Path
from zotero_mcp._app import mcp
from fastmcp import Context


def _build_registry():
    from zotero_mcp.extraction.base import ExtractorRegistry
    from zotero_mcp.extraction.pdfminer_ext import PdfminerExtractor
    from zotero_mcp.extraction.markitdown_ext import MarkItDownExtractor

    registry = ExtractorRegistry()
    registry.register(PdfminerExtractor())
    registry.register(MarkItDownExtractor())
    return registry


@mcp.tool()
def extract_paper_content(
    file_path: str,
    content_type: str = "application/pdf",
    ctx: Context = None,
) -> str:
    """Extract text content from a paper file (PDF or HTML).

    Returns first 500 chars of extracted text plus metadata (backend, quality signal, char count).
    """
    path = Path(file_path)
    if not path.exists():
        return f"Error: File not found: {file_path}"

    registry = _build_registry()

    try:
        content = path.read_bytes()
    except Exception as e:
        return f"Error reading file: {e}"

    from zotero_mcp.extraction.base import NoExtractorError

    try:
        result = registry.extract_with_fallback(content, content_type, {})
    except Exception as e:
        return f"Error during extraction: {e}"

    lines = [
        f"Backend: {result.backend}",
        f"Quality: {result.quality_signal}",
        f"Characters: {result.char_count:,}",
    ]
    if result.fallback_chain:
        lines.append(f"Fallback chain: {'; '.join(result.fallback_chain)}")
    if result.text:
        lines.append(f"\nPreview (first 500 chars):\n{result.text[:500]}")
    else:
        lines.append("\n[No text extracted]")

    return "\n".join(lines)

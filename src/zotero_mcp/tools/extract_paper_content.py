"""extract_paper_content MCP tool — thin wrapper around extraction registry."""

from __future__ import annotations

from pathlib import Path

from fastmcp import Context

from zotero_mcp._app import mcp


def _build_registry():
    from zotero_mcp.acquisition.config import load_acquisition_config
    from zotero_mcp.extraction.base import ExtractorRegistry
    from zotero_mcp.extraction.docling_vlm_ext import DoclingVLMExtractor
    from zotero_mcp.extraction.markitdown_ext import MarkItDownExtractor
    from zotero_mcp.extraction.pdfminer_ext import PdfminerExtractor

    config = load_acquisition_config()
    registry = ExtractorRegistry()
    registry.register(PdfminerExtractor())
    registry.register(DoclingVLMExtractor(config=config))
    registry.register(MarkItDownExtractor())
    return registry, config


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

    registry, config = _build_registry()

    try:
        content = path.read_bytes()
    except Exception as e:
        return f"Error reading file: {e}"

    try:
        result = registry.extract_with_fallback(
            content,
            content_type,
            {"docling_ocr_min_chars": config.extraction.docling_ocr_min_chars},
        )
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

    lines.append("")
    lines.append("[Paper Ingest Provenance - extract step]")
    lines.append(f"extraction_backend: {result.backend}")
    lines.append(f"has_fulltext: {result.quality_signal != 'empty'}")
    if result.fallback_chain:
        lines.append(f"fallback_reason: {result.fallback_chain[0][:100]}")

    return "\n".join(lines)

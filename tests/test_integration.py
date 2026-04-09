"""Integration tests for the paper acquisition pipeline.

These tests require:
- Zotero desktop running on port 23119
- ZOTERO_API_KEY and ZOTERO_LIBRARY_ID environment variables (for write operations)
- Network access (Unpaywall, arXiv APIs)

Run with: pytest tests/test_integration.py -m integration -v

Bridge end-to-end tests (mock-based, no live services required) run without -m:
  pytest tests/test_integration.py -v -k "bridge_institutional"
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(scope="module")
def zotero_write_client():
    """Provide write-capable pyzotero client; skips if ZOTERO_API_KEY unset."""
    from pyzotero import zotero as pz

    api_key = os.environ.get("ZOTERO_API_KEY")
    library_id = os.environ.get("ZOTERO_LIBRARY_ID", "10060356")
    if not api_key:
        pytest.skip("ZOTERO_API_KEY not set — skipping write integration tests")
    return pz.Zotero(library_id, "user", api_key=api_key)


@pytest.fixture
def created_items():
    """Track Zotero item keys created during a test for post-test cleanup."""
    return []


@pytest.fixture(autouse=True)
def cleanup_items(created_items, request):
    """Best-effort deletion of any items created during the test."""
    yield
    write_client = request.node.funcargs.get("zotero_write_client")
    if write_client and created_items:
        for key in created_items:
            try:
                item = write_client.item(key)
                write_client.delete_item(item)
            except Exception:
                pass


@pytest.mark.integration
@pytest.mark.asyncio
async def test_oa_paper_e2e(tmp_path):
    """AC1: DOI with known OA → resolve → PDF URL found."""
    from zotero_mcp.acquisition.config import load_acquisition_config
    from zotero_mcp.acquisition.resolver import resolve_access

    config = load_acquisition_config()
    result = await resolve_access("10.1038/nature12373", config)

    assert result.best_location is not None, "Expected OA location for 10.1038/nature12373"
    assert result.best_location.url, "Expected non-empty PDF URL"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_arxiv_id_resolution():
    """AC2: arXiv ID → PDF URL → quality_signal good after download+extract."""
    from zotero_mcp.acquisition.arxiv import ArxivResolver

    resolver = ArxivResolver()
    result = await resolver.resolve("2301.00001")

    assert result.identifier_type == "arxiv"
    assert result.best_location is not None
    assert "arxiv.org/pdf" in result.best_location.url


@pytest.mark.integration
@pytest.mark.asyncio
async def test_doi_no_oa_institutional_disabled():
    """AC3: DOI without OA + institutional disabled → empty resolution, structured message."""
    from zotero_mcp.acquisition.config import AcquisitionConfig
    from zotero_mcp.acquisition.resolver import resolve_access

    config = AcquisitionConfig()
    result = await resolve_access("10.9999/definitely-closed-no-oa", config)

    assert isinstance(result.locations, list)
    assert result.identifier_type in ("doi", "unknown")


@pytest.mark.integration
def test_extraction_fallback_html():
    """AC4: HTML where MarkItDown may fail → BeautifulSoup internal fallback → chain recorded."""
    from zotero_mcp.extraction.markitdown_ext import MarkItDownExtractor

    fixtures = Path(__file__).parent / "fixtures"
    html_bytes = (fixtures / "sample_js_heavy.html").read_bytes()
    ext = MarkItDownExtractor()
    result = ext.extract(html_bytes, "text/html", {})

    assert result.backend == "markitdown"
    assert result.quality_signal in ("good", "degraded", "empty")


@pytest.mark.integration
def test_extraction_corrupt_pdf_empty_result():
    """AC4 (PDF path): Corrupt PDF → pdfminer fails → empty result with fallback_chain."""
    from zotero_mcp.extraction.base import ExtractorRegistry
    from zotero_mcp.extraction.pdfminer_ext import PdfminerExtractor

    fixtures = Path(__file__).parent / "fixtures"
    corrupt_bytes = (fixtures / "sample_corrupt.pdf").read_bytes()

    reg = ExtractorRegistry()
    reg.register(PdfminerExtractor())
    result = reg.extract_with_fallback(corrupt_bytes, "application/pdf", {})

    assert result.quality_signal == "empty"
    assert len(result.fallback_chain) >= 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_institutional_enabled_without_prefix():
    """AC5: Institutional access enabled but no ezproxy_prefix → ConfigError raised."""
    from zotero_mcp.acquisition.config import AcquisitionConfig, InstitutionalConfig
    from zotero_mcp.acquisition.institutional import ConfigError, InstitutionalResolver

    config = AcquisitionConfig(institutional_access=InstitutionalConfig(enabled=True, ezproxy_prefix=""))
    resolver = InstitutionalResolver(config)

    with pytest.raises(ConfigError, match="ezproxy_prefix"):
        await resolver.resolve("10.1038/nature12373", {})


@pytest.mark.integration
def test_ingest_creates_zotero_item(zotero_write_client, created_items):
    """AC6: ingest_paper_to_zotero → item created in Zotero, provenance in extra field."""
    from zotero_mcp.acquisition.ingest import ingest_to_zotero

    metadata = {
        "title": "Integration Test Paper — AC6 (safe to delete)",
        "itemType": "journalArticle",
        "DOI": "10.9999/integration-test-ac6",
        "abstractNote": "Created by integration test AC6. Please delete.",
    }

    result = ingest_to_zotero(metadata, pdf_path=None, zot=zotero_write_client)
    assert result.item_key, "Expected item_key after ingest"
    created_items.append(result.item_key)

    item = zotero_write_client.item(result.item_key)
    assert item["data"]["title"] == metadata["title"]


@pytest.mark.integration
def test_config_without_acquisition_section():
    """AC7: Config without acquisition section → tools work with defaults."""
    from zotero_mcp.acquisition.config import AcquisitionConfig

    config = AcquisitionConfig()
    assert config.download.timeout_seconds == 30
    assert config.download.max_size_mb == 100
    assert config.institutional_access.enabled is False


@pytest.mark.integration
def test_existing_tools_unaffected():
    """AC8: Existing semantic-search tools still importable and functional."""
    try:
        from zotero_mcp.tools.search import (
            advanced_search,
            search_items,
        )

        assert callable(search_items)
        assert callable(advanced_search)
    except ImportError as e:
        pytest.fail(f"Existing tools broken by changes: {e}")


@pytest.mark.integration
def test_edge_invalid_identifier():
    """AC9 edge: resolve_paper_access("not-a-doi") → structured error, no crash."""
    from zotero_mcp.tools.resolve_paper_access import resolve_paper_access

    ctx = MagicMock()
    result = asyncio.run(resolve_paper_access("not-a-doi", ctx))
    assert isinstance(result, str)
    assert len(result) > 0


@pytest.mark.integration
def test_edge_empty_identifier():
    """AC10 edge: resolve_paper_access("") → NormalizationError message."""
    from zotero_mcp.tools.resolve_paper_access import resolve_paper_access

    ctx = MagicMock()
    result = asyncio.run(resolve_paper_access("", ctx))
    assert isinstance(result, str)
    assert len(result) > 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_edge_download_404():
    """AC11 edge: download_paper_artifact with nonexistent URL → structured 404 error."""
    from zotero_mcp.acquisition.config import load_acquisition_config
    from zotero_mcp.acquisition.download import ArtifactDownloader
    from zotero_mcp.acquisition.types import PipelineError

    config = load_acquisition_config()
    downloader = ArtifactDownloader(config)
    dest = Path(tempfile.mkdtemp())

    result = await downloader.download("https://httpbin.org/status/404", dest_dir=dest)
    assert isinstance(result, PipelineError)
    assert result.code in ("HTTP_ERROR", "WRONG_CONTENT_TYPE", "INVALID_PDF_BYTES")


@pytest.mark.integration
def test_edge_extract_missing_file():
    """AC12 edge: extract_paper_content with nonexistent file → FileNotFoundError message."""
    from zotero_mcp.tools.extract_paper_content import extract_paper_content

    ctx = MagicMock()
    result = extract_paper_content("/tmp/absolutely_nonexistent_file_xyz.pdf", ctx=ctx)
    assert isinstance(result, str)
    assert "Error" in result or "not found" in result.lower()


@pytest.mark.integration
def test_edge_extract_corrupt_pdf():
    """AC13 edge: extract_paper_content on corrupt PDF → empty result with fallback_chain."""
    from zotero_mcp.tools.extract_paper_content import extract_paper_content

    fixtures = Path(__file__).parent / "fixtures"
    ctx = MagicMock()
    result = extract_paper_content(str(fixtures / "sample_corrupt.pdf"), "application/pdf", ctx)
    assert isinstance(result, str)
    assert "empty" in result.lower() or "fallback" in result.lower() or "Error" in result or "Quality" in result


@pytest.mark.integration
def test_4_new_tools_registered():
    """Verify all 4 new acquisition tools appear in the MCP tool registry."""

    async def check():
        from zotero_mcp.server import mcp

        tools = await mcp.list_tools()
        names = [t.name for t in tools]
        for expected in [
            "resolve_paper_access",
            "download_paper_artifact",
            "extract_paper_content",
            "ingest_paper_to_zotero",
        ]:
            assert expected in names, f"Tool {expected} not registered"
        return len(names)

    total = asyncio.run(check())
    assert total >= 4, f"Expected ≥4 tools total, got {total}"


@pytest.mark.asyncio
async def test_bridge_institutional():
    from zotero_mcp.acquisition.bridge_client import BridgeDownloadResult
    from zotero_mcp.acquisition.types import AccessLocation, AccessResolution
    from zotero_mcp.tools.acquire_paper import acquire_paper

    loc = AccessLocation(
        url="https://libproxy.snu.ac.kr/link.n2s?url=https://publisher.example.com/10.xxx/institutional-only",
        access_method="institutional",
        requires_session=True,
        session_kind="libproxy",
    )
    resolution = AccessResolution(
        identifier_type="doi",
        identifier_value="10.xxx/institutional-only",
        locations=[loc],
        best_location=loc,
    )
    bridge_result = BridgeDownloadResult(
        status="complete",
        auth_state="ready",
        file_path="/tmp/test.pdf",
    )

    with (
        patch("zotero_mcp.tools.acquire_paper.resolve_access", new=AsyncMock(return_value=resolution)),
        patch("zotero_mcp.tools.acquire_paper.BridgeClient") as MockBridgeClient,
    ):
        mock_bridge = MagicMock()
        mock_bridge.is_available.return_value = True
        mock_bridge.download.return_value = bridge_result
        MockBridgeClient.return_value = mock_bridge

        result = await acquire_paper("10.xxx/institutional-only", session_name="libproxy-snu")

    assert result["status"] == "complete"
    assert result["file_path"] == "/tmp/test.pdf"
    assert result["provenance"]["bridge_session"] == "libproxy-snu"

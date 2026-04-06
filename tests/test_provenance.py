from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from zotero_mcp.acquisition.types import AccessResolution, AccessLocation
from zotero_mcp.tools.resolve_paper_access import resolve_paper_access
from zotero_mcp.tools.extract_paper_content import extract_paper_content
from zotero_mcp.tools.ingest_paper_to_zotero import ingest_paper_to_zotero

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def mock_ctx():
    return MagicMock()


def _oa_resolution(identifier_type="doi", license_str="cc-by"):
    loc = AccessLocation(url="https://example.com/paper.pdf", access_method="oa", license=license_str)
    return AccessResolution(
        identifier_type=identifier_type,
        identifier_value="10.xxx/test",
        locations=[loc],
        best_location=loc,
        metadata={},
    )


def _make_mock_zotero(item_key="TEST_KEY"):
    zot = MagicMock()
    zot.item_template.return_value = {
        "itemType": "journalArticle",
        "title": "",
        "DOI": "",
        "date": "",
        "publicationTitle": "",
        "abstractNote": "",
        "creators": [],
        "extra": "",
    }
    zot.items.return_value = []
    zot.create_items.return_value = {"successful": {"0": {"key": item_key}}, "failed": {}}
    zot.children.return_value = []
    return zot


@pytest.mark.asyncio
class TestResolveProvenance:
    async def test_resolve_output_includes_provenance_section(self, mock_ctx):
        with patch(
            "zotero_mcp.acquisition.resolver.resolve_access",
            new=AsyncMock(return_value=_oa_resolution()),
        ):
            result = await resolve_paper_access("10.xxx/test", mock_ctx)
        assert "[Paper Ingest Provenance - resolve step]" in result

    async def test_resolve_access_source_matches_identifier_type(self, mock_ctx):
        with patch(
            "zotero_mcp.acquisition.resolver.resolve_access",
            new=AsyncMock(return_value=_oa_resolution("doi")),
        ):
            result = await resolve_paper_access("10.xxx/test", mock_ctx)
        assert "access_source: doi" in result

    async def test_resolve_unpaywall_resolver_name_for_doi(self, mock_ctx):
        with patch(
            "zotero_mcp.acquisition.resolver.resolve_access",
            new=AsyncMock(return_value=_oa_resolution("doi", "cc-by-4.0")),
        ):
            result = await resolve_paper_access("10.xxx/test", mock_ctx)
        assert "resolver_name: Unpaywall OA" in result
        assert "license: cc-by-4.0" in result

    async def test_resolve_arxiv_api_resolver_name(self, mock_ctx):
        loc = AccessLocation(url="https://arxiv.org/pdf/2301.00001.pdf", access_method="oa")
        resolution = AccessResolution(
            identifier_type="arxiv",
            identifier_value="2301.00001",
            locations=[loc],
            best_location=loc,
            metadata={},
        )
        with patch(
            "zotero_mcp.acquisition.resolver.resolve_access",
            new=AsyncMock(return_value=resolution),
        ):
            result = await resolve_paper_access("2301.00001", mock_ctx)
        assert "access_source: arxiv" in result
        assert "resolver_name: arXiv API" in result

    async def test_resolve_no_best_location_omits_resolver_name(self, mock_ctx):
        resolution = AccessResolution(
            identifier_type="doi",
            identifier_value="10.xxx/closed",
            locations=[],
            best_location=None,
            metadata={},
        )
        with patch(
            "zotero_mcp.acquisition.resolver.resolve_access",
            new=AsyncMock(return_value=resolution),
        ):
            result = await resolve_paper_access("10.xxx/closed", mock_ctx)
        assert "[Paper Ingest Provenance - resolve step]" in result
        assert "resolver_name" not in result


class TestExtractProvenance:
    def test_extract_includes_provenance_section(self, mock_ctx):
        result = extract_paper_content(str(FIXTURES / "sample_text.pdf"), "application/pdf", mock_ctx)
        assert "[Paper Ingest Provenance - extract step]" in result

    def test_extract_backend_field_populated(self, mock_ctx):
        result = extract_paper_content(str(FIXTURES / "sample_text.pdf"), "application/pdf", mock_ctx)
        assert "extraction_backend: pdfminer" in result

    def test_extract_has_fulltext_present_for_real_pdf(self, mock_ctx):
        result = extract_paper_content(str(FIXTURES / "sample_text.pdf"), "application/pdf", mock_ctx)
        assert "has_fulltext: True" in result or "has_fulltext: False" in result

    def test_extract_has_fulltext_false_for_corrupt_pdf(self, mock_ctx):
        result = extract_paper_content(str(FIXTURES / "sample_corrupt.pdf"), "application/pdf", mock_ctx)
        assert "has_fulltext: False" in result or "Error" in result or "empty" in result


class TestIngestProvenance:
    def test_provenance_string_written_to_extra(self, mock_ctx):
        mock_zot = _make_mock_zotero()
        prov_str = "access_source: unpaywall\nresolver_name: Unpaywall OA\nlicense: cc-by"
        with patch(
            "zotero_mcp.tools.ingest_paper_to_zotero._get_write_client",
            return_value=(mock_zot, mock_zot),
        ):
            result = ingest_paper_to_zotero("Test Paper", doi="10.xxx/test", provenance=prov_str, ctx=mock_ctx)
        assert isinstance(result, str)
        mock_zot.create_items.assert_called_once()
        extra = mock_zot.create_items.call_args[0][0][0].get("extra", "")
        assert "[Paper Ingest Provenance]" in extra
        assert "access_source: unpaywall" in extra

    def test_provenance_resolver_name_in_extra(self, mock_ctx):
        mock_zot = _make_mock_zotero()
        prov_str = "access_source: unpaywall\nresolver_name: Unpaywall OA"
        with patch(
            "zotero_mcp.tools.ingest_paper_to_zotero._get_write_client",
            return_value=(mock_zot, mock_zot),
        ):
            ingest_paper_to_zotero("Test Paper", doi="10.xxx/test", provenance=prov_str, ctx=mock_ctx)
        extra = mock_zot.create_items.call_args[0][0][0].get("extra", "")
        assert "resolver_name: Unpaywall OA" in extra

    def test_provenance_bool_has_fulltext_parsed(self, mock_ctx):
        mock_zot = _make_mock_zotero()
        prov_str = "has_fulltext: true\nextraction_backend: pdfminer"
        with patch(
            "zotero_mcp.tools.ingest_paper_to_zotero._get_write_client",
            return_value=(mock_zot, mock_zot),
        ):
            ingest_paper_to_zotero("Test Paper", doi="10.xxx/test", provenance=prov_str, ctx=mock_ctx)
        extra = mock_zot.create_items.call_args[0][0][0].get("extra", "")
        assert "has_fulltext: True" in extra
        assert "extraction_backend: pdfminer" in extra

    def test_provenance_none_still_writes_header(self, mock_ctx):
        mock_zot = _make_mock_zotero()
        with patch(
            "zotero_mcp.tools.ingest_paper_to_zotero._get_write_client",
            return_value=(mock_zot, mock_zot),
        ):
            ingest_paper_to_zotero("No Provenance Paper", doi="10.xxx/noprov", ctx=mock_ctx)
        extra = mock_zot.create_items.call_args[0][0][0].get("extra", "")
        assert "[Paper Ingest Provenance]" in extra

    def test_provenance_full_g8_roundtrip(self, mock_ctx):
        mock_zot = _make_mock_zotero()
        prov_str = (
            "access_source: unpaywall\n"
            "resolver_name: Unpaywall OA\n"
            "artifact_type: pdf\n"
            "extraction_backend: pdfminer\n"
            "license: cc-by\n"
            "has_fulltext: true"
        )
        with patch(
            "zotero_mcp.tools.ingest_paper_to_zotero._get_write_client",
            return_value=(mock_zot, mock_zot),
        ):
            ingest_paper_to_zotero("Full Roundtrip", doi="10.xxx/test", provenance=prov_str, ctx=mock_ctx)
        extra = mock_zot.create_items.call_args[0][0][0].get("extra", "")
        assert "[Paper Ingest Provenance]" in extra
        assert "access_source: unpaywall" in extra
        assert "resolver_name: Unpaywall OA" in extra
        assert "artifact_type: pdf" in extra
        assert "extraction_backend: pdfminer" in extra
        assert "license: cc-by" in extra
        assert "has_fulltext: True" in extra

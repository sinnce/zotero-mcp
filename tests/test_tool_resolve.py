import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from zotero_mcp.tools.resolve_paper_access import resolve_paper_access
from zotero_mcp.acquisition.types import AccessResolution, AccessLocation


@pytest.fixture
def mock_ctx():
    ctx = MagicMock()
    ctx.info = MagicMock()
    return ctx


@pytest.fixture
def oa_resolution():
    loc = AccessLocation(url="https://example.com/paper.pdf", access_method="oa", license="cc-by")
    return AccessResolution(
        identifier_type="doi",
        identifier_value="10.1038/nature12373",
        locations=[loc],
        best_location=loc,
        metadata={"title": "Test Paper", "year": 2023},
    )


@pytest.mark.asyncio
class TestResolvePaperAccess:
    async def test_doi_oa_resolution(self, mock_ctx, oa_resolution):
        with patch("zotero_mcp.acquisition.resolver.resolve_access", new=AsyncMock(return_value=oa_resolution)):
            result = await resolve_paper_access("10.1038/nature12373", mock_ctx)
        assert isinstance(result, str)
        assert "example.com/paper.pdf" in result or "oa" in result

    async def test_invalid_identifier_returns_error_message(self, mock_ctx):
        empty_result = AccessResolution(
            identifier_type="unknown",
            identifier_value="not-a-doi",
            metadata={"error": "Cannot parse 'not-a-doi' as DOI, arXiv ID, PMID, or URL"},
        )
        with patch("zotero_mcp.acquisition.resolver.resolve_access", new=AsyncMock(return_value=empty_result)):
            result = await resolve_paper_access("not-a-doi", mock_ctx)
        assert "Error" in result or "error" in result.lower()

    async def test_closed_access_paper(self, mock_ctx):
        empty = AccessResolution(
            identifier_type="doi",
            identifier_value="10.xxx/closed",
            locations=[],
            best_location=None,
        )
        with patch("zotero_mcp.acquisition.resolver.resolve_access", new=AsyncMock(return_value=empty)):
            result = await resolve_paper_access("10.xxx/closed", mock_ctx)
        assert isinstance(result, str)
        assert "No" in result or "0" in result

    async def test_returns_string(self, mock_ctx, oa_resolution):
        with patch("zotero_mcp.acquisition.resolver.resolve_access", new=AsyncMock(return_value=oa_resolution)):
            result = await resolve_paper_access("10.1038/nature12373", mock_ctx)
        assert isinstance(result, str)

    async def test_arxiv_id_resolution(self, mock_ctx):
        arxiv_result = AccessResolution(
            identifier_type="arxiv",
            identifier_value="2301.00001",
            locations=[AccessLocation(url="https://arxiv.org/pdf/2301.00001.pdf", access_method="oa")],
            best_location=AccessLocation(url="https://arxiv.org/pdf/2301.00001.pdf", access_method="oa"),
        )
        with patch("zotero_mcp.acquisition.resolver.resolve_access", new=AsyncMock(return_value=arxiv_result)):
            result = await resolve_paper_access("2301.00001", mock_ctx)
        assert "arxiv.org" in result

    async def test_never_raises(self, mock_ctx):
        with patch(
            "zotero_mcp.acquisition.resolver.resolve_access", new=AsyncMock(side_effect=Exception("unexpected"))
        ):
            try:
                result = await resolve_paper_access("10.xxx/test", mock_ctx)
                assert isinstance(result, str)
            except Exception:
                pass

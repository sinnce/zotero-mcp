import time
import pytest
from pathlib import Path
from pytest_httpx import HTTPXMock
from zotero_mcp.acquisition.arxiv import ArxivResolver
from zotero_mcp.acquisition.types import AccessResolution

FIXTURES = Path(__file__).parent / "fixtures"
XML = (FIXTURES / "arxiv_response.xml").read_text()


@pytest.fixture
def resolver():
    return ArxivResolver(rate_limit_seconds=0.0)


@pytest.mark.asyncio
class TestArxivResolver:
    async def test_resolve_returns_access_resolution(self, resolver, httpx_mock: HTTPXMock):
        httpx_mock.add_response(text=XML, headers={"content-type": "application/atom+xml"})
        result = await resolver.resolve("2301.00001")
        assert isinstance(result, AccessResolution)
        assert result.identifier_type == "arxiv"

    async def test_pdf_url_constructed(self, resolver, httpx_mock: HTTPXMock):
        httpx_mock.add_response(text=XML, headers={"content-type": "application/atom+xml"})
        result = await resolver.resolve("2301.00001")
        assert result.best_location is not None
        assert "arxiv.org/pdf/2301.00001" in result.best_location.url

    async def test_metadata_title_authors(self, resolver, httpx_mock: HTTPXMock):
        httpx_mock.add_response(text=XML, headers={"content-type": "application/atom+xml"})
        result = await resolver.resolve("2301.00001")
        assert result.metadata.get("title") is not None
        assert result.metadata.get("authors") is not None

    async def test_old_format_id(self, resolver, httpx_mock: HTTPXMock):
        httpx_mock.add_response(text=XML, headers={"content-type": "application/atom+xml"})
        result = await resolver.resolve("hep-ph/0601001")
        assert result.identifier_type == "arxiv"

    async def test_pdf_url_helper(self, resolver):
        url = resolver.pdf_url("2301.00001")
        assert url == "https://arxiv.org/pdf/2301.00001.pdf"

    async def test_source_url_helper(self, resolver):
        url = resolver.source_url("2301.00001")
        assert url == "https://arxiv.org/e-print/2301.00001"

    async def test_never_raises(self, resolver, httpx_mock: HTTPXMock):
        import httpx

        httpx_mock.add_exception(httpx.NetworkError("no network"))
        result = await resolver.resolve("2301.00001")
        assert isinstance(result, AccessResolution)

    async def test_rate_limit_enforced(self):
        resolver = ArxivResolver(rate_limit_seconds=0.1)
        call_times = []

        async def mock_fetch(arxiv_id):
            call_times.append(time.monotonic())
            return AccessResolution(identifier_type="arxiv", identifier_value=arxiv_id)

        resolver._fetch = mock_fetch
        await resolver.resolve("2301.00001")
        await resolver.resolve("2301.00002")
        if len(call_times) >= 2:
            gap = call_times[1] - call_times[0]
            assert gap >= 0.09, f"Rate limit not enforced: gap was {gap:.3f}s"

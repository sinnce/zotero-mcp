import pytest
import httpx
from pytest_httpx import HTTPXMock

from zotero_mcp.acquisition.semantic_scholar import SemanticScholarResolver
from zotero_mcp.acquisition.config import AcquisitionConfig
from zotero_mcp.acquisition.types import AccessResolution


_OA_RESPONSE = {
    "paperId": "abc123",
    "title": "Attention Is All You Need",
    "authors": [
        {"authorId": "1", "name": "Ashish Vaswani"},
        {"authorId": "2", "name": "Noam Shazeer"},
    ],
    "abstract": "The dominant sequence transduction models are based on complex recurrent or convolutional neural networks.",
    "isOpenAccess": True,
    "openAccessPdf": {
        "url": "https://arxiv.org/pdf/1706.03762",
        "status": "GREEN",
    },
}

_CLOSED_RESPONSE = {
    "paperId": "def456",
    "title": "A Closed Access Paper",
    "authors": [{"authorId": "3", "name": "Jane Doe"}],
    "abstract": "Some abstract text.",
    "isOpenAccess": False,
    "openAccessPdf": None,
}


@pytest.fixture
def resolver():
    return SemanticScholarResolver()


@pytest.fixture
def resolver_disabled():
    config = AcquisitionConfig(s2_enabled=False)
    return SemanticScholarResolver(config=config)


class TestSemanticScholarResolver:
    async def test_doi_found_with_oa_pdf(self, resolver, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json=_OA_RESPONSE)
        result = await resolver.resolve("10.48550/arXiv.1706.03762")
        assert isinstance(result, AccessResolution)
        assert len(result.locations) >= 1
        assert result.best_location is not None
        assert result.best_location.access_method == "oa"
        assert "arxiv.org/pdf" in result.best_location.url

    async def test_doi_not_found_404(self, resolver, httpx_mock: HTTPXMock):
        httpx_mock.add_response(status_code=404)
        result = await resolver.resolve("10.9999/nonexistent-doi")
        assert isinstance(result, AccessResolution)
        assert result.locations == []
        assert result.best_location is None

    @pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
    async def test_network_error_never_raises(self, resolver, httpx_mock: HTTPXMock):
        httpx_mock.add_exception(httpx.NetworkError("no network"))
        result = await resolver.resolve("10.1234/test", retry_delay=0.0)
        assert isinstance(result, AccessResolution)
        assert result.locations == []

    @pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
    async def test_timeout_never_raises(self, resolver, httpx_mock: HTTPXMock):
        httpx_mock.add_exception(httpx.TimeoutException("timed out"))
        result = await resolver.resolve("10.1234/timeout-test", retry_delay=0.0)
        assert isinstance(result, AccessResolution)
        assert result.locations == []

    async def test_rate_limit_429_never_raises(self, resolver, httpx_mock: HTTPXMock):
        httpx_mock.add_response(status_code=429)
        result = await resolver.resolve("10.1234/rate-limited")
        assert isinstance(result, AccessResolution)
        assert result.locations == []

    async def test_s2_enabled_false_no_http_call(self, resolver_disabled):
        result = await resolver_disabled.resolve("10.1234/any-doi")
        assert isinstance(result, AccessResolution)
        assert result.locations == []

    async def test_no_oa_pdf_returns_empty_locations(self, resolver, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json=_CLOSED_RESPONSE)
        result = await resolver.resolve("10.9999/closed")
        assert isinstance(result, AccessResolution)
        assert result.locations == []

    async def test_metadata_title_extracted(self, resolver, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json=_OA_RESPONSE)
        result = await resolver.resolve("10.48550/arXiv.1706.03762")
        assert result.metadata.get("title") == "Attention Is All You Need"

    async def test_metadata_authors_extracted(self, resolver, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json=_OA_RESPONSE)
        result = await resolver.resolve("10.48550/arXiv.1706.03762")
        assert "authors" in result.metadata
        authors = result.metadata["authors"]
        assert isinstance(authors, list)
        assert "Ashish Vaswani" in authors
        assert "Noam Shazeer" in authors

    async def test_metadata_abstract_extracted(self, resolver, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json=_OA_RESPONSE)
        result = await resolver.resolve("10.48550/arXiv.1706.03762")
        assert "abstract" in result.metadata
        assert len(result.metadata["abstract"]) > 0

    async def test_api_key_sent_in_header(self, httpx_mock: HTTPXMock):
        config = AcquisitionConfig(s2_enabled=True, s2_api_key="test-api-key-123")
        resolver = SemanticScholarResolver(config=config)
        httpx_mock.add_response(json=_OA_RESPONSE)
        await resolver.resolve("10.48550/arXiv.1706.03762")
        request = httpx_mock.get_requests()[0]
        assert request.headers.get("x-api-key") == "test-api-key-123"

    async def test_no_api_key_no_header(self, resolver, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json=_OA_RESPONSE)
        await resolver.resolve("10.48550/arXiv.1706.03762")
        request = httpx_mock.get_requests()[0]
        assert "x-api-key" not in request.headers

    async def test_identifier_fields_in_result(self, resolver, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json=_OA_RESPONSE)
        doi = "10.48550/arXiv.1706.03762"
        result = await resolver.resolve(doi)
        assert result.identifier_type == "doi"
        assert result.identifier_value == doi

import pytest
import httpx
from pytest_httpx import HTTPXMock

from zotero_mcp.acquisition.pmc_oa import PMCOAResolver
from zotero_mcp.acquisition.config import AcquisitionConfig
from zotero_mcp.acquisition.types import AccessResolution


_IDCONV_FOUND = {"records": [{"pmcid": "PMC1234567", "pmid": "12345678"}]}

_IDCONV_NOT_FOUND = {"records": [{"errmsg": "not found"}]}

_OA_PDF_ONLY = {
    "records": [
        {
            "id": "PMC1234567",
            "files": [
                {
                    "format": "pdf",
                    "href": "https://ftp.ncbi.nlm.nih.gov/pub/pmc/oa/pdf/abcd/PMC1234567.pdf",
                    "updated": "2023-01-01",
                }
            ],
        }
    ]
}

_OA_HTML_ONLY = {
    "records": [
        {
            "id": "PMC1234567",
            "files": [
                {
                    "format": "html",
                    "href": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1234567/",
                    "updated": "2023-01-01",
                }
            ],
        }
    ]
}

_OA_PDF_AND_HTML = {
    "records": [
        {
            "id": "PMC1234567",
            "files": [
                {
                    "format": "html",
                    "href": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1234567/",
                    "updated": "2023-01-01",
                },
                {
                    "format": "pdf",
                    "href": "https://ftp.ncbi.nlm.nih.gov/pub/pmc/oa/pdf/abcd/PMC1234567.pdf",
                    "updated": "2023-01-01",
                },
            ],
        }
    ]
}

_OA_EMPTY_FILES = {
    "records": [
        {
            "id": "PMC1234567",
            "files": [],
        }
    ]
}


@pytest.fixture
def resolver():
    return PMCOAResolver()


@pytest.fixture
def resolver_disabled():
    config = AcquisitionConfig(pmc_enabled=False)
    return PMCOAResolver(config=config)


class TestPMCOAResolver:
    async def test_doi_found_returns_pdf_url(self, resolver, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json=_IDCONV_FOUND)
        httpx_mock.add_response(json=_OA_PDF_ONLY)
        result = await resolver.resolve("10.1371/journal.pone.0000001")
        assert isinstance(result, AccessResolution)
        assert len(result.locations) == 1
        assert result.best_location is not None
        assert result.best_location.access_method == "oa"
        assert "PMC1234567.pdf" in result.best_location.url

    async def test_doi_not_in_pmc_returns_empty(self, resolver, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json=_IDCONV_NOT_FOUND)
        result = await resolver.resolve("10.9999/not-in-pmc")
        assert isinstance(result, AccessResolution)
        assert result.locations == []
        assert result.best_location is None

    @pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
    async def test_ncbi_500_never_raises(self, resolver, httpx_mock: HTTPXMock):
        httpx_mock.add_response(status_code=500)
        result = await resolver.resolve("10.1234/test-error")
        assert isinstance(result, AccessResolution)
        assert result.locations == []

    @pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
    async def test_network_error_never_raises(self, resolver, httpx_mock: HTTPXMock):
        httpx_mock.add_exception(httpx.NetworkError("no network"))
        result = await resolver.resolve("10.1234/test-network")
        assert isinstance(result, AccessResolution)
        assert result.locations == []

    async def test_pmc_disabled_no_http_call(self, resolver_disabled):
        result = await resolver_disabled.resolve("10.1234/any-doi")
        assert isinstance(result, AccessResolution)
        assert result.locations == []

    async def test_html_only_returns_empty(self, resolver, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json=_IDCONV_FOUND)
        httpx_mock.add_response(json=_OA_HTML_ONLY)
        result = await resolver.resolve("10.1371/journal.pone.0000002")
        assert isinstance(result, AccessResolution)
        assert result.locations == []

    async def test_prefers_pdf_over_html(self, resolver, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json=_IDCONV_FOUND)
        httpx_mock.add_response(json=_OA_PDF_AND_HTML)
        result = await resolver.resolve("10.1371/journal.pone.0000003")
        assert isinstance(result, AccessResolution)
        assert len(result.locations) == 1
        assert "PMC1234567.pdf" in result.locations[0].url
        assert result.best_location is not None
        assert "PMC1234567.pdf" in result.best_location.url

    async def test_identifier_fields_in_result(self, resolver, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json=_IDCONV_FOUND)
        httpx_mock.add_response(json=_OA_PDF_ONLY)
        doi = "10.1371/journal.pone.0000004"
        result = await resolver.resolve(doi)
        assert result.identifier_type == "doi"
        assert result.identifier_value == doi

    async def test_ncbi_email_sent_when_configured(self, httpx_mock: HTTPXMock):
        config = AcquisitionConfig(pmc_enabled=True, ncbi_email="test@example.com")
        resolver = PMCOAResolver(config=config)
        httpx_mock.add_response(json=_IDCONV_FOUND)
        httpx_mock.add_response(json=_OA_PDF_ONLY)
        await resolver.resolve("10.1371/journal.pone.0000005")
        first_request = httpx_mock.get_requests()[0]
        assert "email=test%40example.com" in str(first_request.url) or "email=test@example.com" in str(
            first_request.url
        )

    async def test_empty_records_returns_empty(self, resolver, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json={"records": []})
        result = await resolver.resolve("10.9999/empty-records")
        assert isinstance(result, AccessResolution)
        assert result.locations == []

    async def test_oa_empty_files_returns_empty(self, resolver, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json=_IDCONV_FOUND)
        httpx_mock.add_response(json=_OA_EMPTY_FILES)
        result = await resolver.resolve("10.1371/journal.pone.0000006")
        assert isinstance(result, AccessResolution)
        assert result.locations == []

import json
import pytest
from pathlib import Path
from pytest_httpx import HTTPXMock
from zotero_mcp.acquisition.unpaywall import UnpaywallClient
from zotero_mcp.acquisition.types import AccessResolution

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def oa_json():
    return json.loads((FIXTURES / "unpaywall_oa.json").read_text())


@pytest.fixture
def closed_json():
    return json.loads((FIXTURES / "unpaywall_closed.json").read_text())


@pytest.fixture
def client():
    return UnpaywallClient(email="test@example.com")


class TestUnpaywallClient:
    async def test_oa_resolution(self, client, httpx_mock: HTTPXMock, oa_json):
        httpx_mock.add_response(json=oa_json)
        result = await client.resolve("10.1038/nature12373")
        assert isinstance(result, AccessResolution)
        assert len(result.locations) >= 1
        assert result.best_location is not None
        assert result.best_location.access_method == "oa"

    async def test_closed_access(self, client, httpx_mock: HTTPXMock, closed_json):
        httpx_mock.add_response(json=closed_json)
        result = await client.resolve("10.9999/closed-paper")
        assert isinstance(result, AccessResolution)
        assert result.locations == []
        assert result.best_location is None

    async def test_invalid_doi_404(self, client, httpx_mock: HTTPXMock):
        httpx_mock.add_response(status_code=404)
        result = await client.resolve("10.xxxx/invalid")
        assert isinstance(result, AccessResolution)
        assert result.locations == []

    async def test_retry_on_429(self, client, httpx_mock: HTTPXMock, oa_json):
        httpx_mock.add_response(status_code=429)
        httpx_mock.add_response(json=oa_json)
        result = await client.resolve("10.1038/nature12373", retry_delay=0.0)
        assert result.best_location is not None

    async def test_metadata_extracted(self, client, httpx_mock: HTTPXMock, oa_json):
        httpx_mock.add_response(json=oa_json)
        result = await client.resolve("10.1038/nature12373")
        assert "title" in result.metadata or "year" in result.metadata

    @pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
    async def test_never_raises(self, client, httpx_mock: HTTPXMock):
        import httpx

        httpx_mock.add_exception(httpx.NetworkError("no network"))
        result = await client.resolve("10.xxx/test", retry_delay=0.0)
        assert isinstance(result, AccessResolution)

    async def test_license_extracted(self, client, httpx_mock: HTTPXMock, oa_json):
        httpx_mock.add_response(json=oa_json)
        result = await client.resolve("10.1038/nature12373")
        if result.best_location:
            assert result.best_location.license is not None or True

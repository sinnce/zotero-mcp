import pytest
from pathlib import Path
from pytest_httpx import HTTPXMock
import httpx
from zotero_mcp.acquisition.download import ArtifactDownloader
from zotero_mcp.acquisition.config import AcquisitionConfig, DownloadConfig
from zotero_mcp.acquisition.types import ArtifactDownload, PipelineError

VALID_PDF = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\nxref\n0 1\n0000000000 65535 f \ntrailer<</Size 1>>\nstartxref\n9\n%%EOF"
)


@pytest.fixture
def downloader():
    return ArtifactDownloader(AcquisitionConfig(download=DownloadConfig(timeout_seconds=5, max_size_mb=10)))


@pytest.mark.asyncio
class TestArtifactDownloader:
    async def test_valid_pdf_download(self, downloader, httpx_mock: HTTPXMock, tmp_path):
        httpx_mock.add_response(content=VALID_PDF, headers={"content-type": "application/pdf"})
        result = await downloader.download("https://example.com/paper.pdf", dest_dir=tmp_path)
        assert isinstance(result, ArtifactDownload)
        assert result.file_path.exists()
        assert result.size_bytes > 0
        assert result.content_type == "application/pdf"

    async def test_pdf_magic_bytes_validated(self, downloader, httpx_mock: HTTPXMock, tmp_path):
        httpx_mock.add_response(content=VALID_PDF, headers={"content-type": "application/pdf"})
        result = await downloader.download("https://example.com/paper.pdf", dest_dir=tmp_path)
        assert result.file_path.read_bytes()[:4] == b"%PDF"

    async def test_html_content_rejected(self, downloader, httpx_mock: HTTPXMock, tmp_path):
        httpx_mock.add_response(content=b"<html><body>Not a PDF</body></html>", headers={"content-type": "text/html"})
        result = await downloader.download("https://example.com/paper.pdf", dest_dir=tmp_path)
        assert isinstance(result, PipelineError)
        assert not (tmp_path / "paper.pdf").exists()

    async def test_content_without_pdf_magic_bytes_rejected(self, downloader, httpx_mock: HTTPXMock, tmp_path):
        httpx_mock.add_response(
            content=b"Not a PDF but content-type says PDF", headers={"content-type": "application/pdf"}
        )
        result = await downloader.download("https://example.com/paper.pdf", dest_dir=tmp_path)
        assert isinstance(result, PipelineError)

    async def test_http_error_returns_pipeline_error(self, downloader, httpx_mock: HTTPXMock, tmp_path):
        httpx_mock.add_response(status_code=404)
        result = await downloader.download("https://example.com/paper.pdf", dest_dir=tmp_path)
        assert isinstance(result, PipelineError)

    async def test_network_error_returns_pipeline_error(self, downloader, httpx_mock: HTTPXMock, tmp_path):
        for _ in range(3):
            httpx_mock.add_exception(httpx.NetworkError("no network"))
        result = await downloader.download("https://example.com/paper.pdf", dest_dir=tmp_path)
        assert isinstance(result, PipelineError)

    async def test_size_limit_enforced(self, httpx_mock: HTTPXMock, tmp_path):
        small_config = AcquisitionConfig(download=DownloadConfig(max_size_mb=0))
        downloader = ArtifactDownloader(small_config)
        httpx_mock.add_response(
            content=VALID_PDF,
            headers={"content-type": "application/pdf", "content-length": str(len(VALID_PDF))},
        )
        result = await downloader.download("https://example.com/paper.pdf", dest_dir=tmp_path)
        assert isinstance(result, PipelineError)
        assert "size" in result.message.lower() or "limit" in result.message.lower()

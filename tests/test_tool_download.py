import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock
from zotero_mcp.tools.download_paper_artifact import download_paper_artifact
from zotero_mcp.acquisition.types import ArtifactDownload, PipelineError, ProvenanceMetadata


@pytest.fixture
def mock_ctx():
    return MagicMock()


VALID_PDF = b"%PDF-1.4\ncontent"


@pytest.mark.asyncio
class TestDownloadPaperArtifact:
    async def test_successful_download(self, mock_ctx, tmp_path):
        mock_artifact = ArtifactDownload(
            file_path=tmp_path / "paper.pdf",
            content_type="application/pdf",
            size_bytes=len(VALID_PDF),
        )
        (tmp_path / "paper.pdf").write_bytes(VALID_PDF)

        with patch(
            "zotero_mcp.acquisition.download.ArtifactDownloader.download", new=AsyncMock(return_value=mock_artifact)
        ):
            result = await download_paper_artifact("https://example.com/paper.pdf", ctx=mock_ctx)
        assert isinstance(result, str)
        assert "Downloaded" in result or str(mock_artifact.size_bytes) in result

    async def test_non_pdf_content_error(self, mock_ctx):
        error = PipelineError(step="download", code="WRONG_CONTENT_TYPE", message="Expected PDF", recoverable=False)
        with patch("zotero_mcp.acquisition.download.ArtifactDownloader.download", new=AsyncMock(return_value=error)):
            result = await download_paper_artifact("https://example.com/paper.pdf", ctx=mock_ctx)
        assert isinstance(result, str)
        assert "failed" in result.lower() or "error" in result.lower() or "WRONG_CONTENT_TYPE" in result

    async def test_http_error(self, mock_ctx):
        error = PipelineError(step="download", code="HTTP_ERROR", message="404 Not Found", recoverable=False)
        with patch("zotero_mcp.acquisition.download.ArtifactDownloader.download", new=AsyncMock(return_value=error)):
            result = await download_paper_artifact("https://example.com/notfound.pdf", ctx=mock_ctx)
        assert "failed" in result.lower() or "HTTP_ERROR" in result

    async def test_returns_string(self, mock_ctx, tmp_path):
        mock_artifact = ArtifactDownload(
            file_path=tmp_path / "paper.pdf",
            content_type="application/pdf",
            size_bytes=100,
        )
        with patch(
            "zotero_mcp.acquisition.download.ArtifactDownloader.download", new=AsyncMock(return_value=mock_artifact)
        ):
            result = await download_paper_artifact("https://example.com/paper.pdf", ctx=mock_ctx)
        assert isinstance(result, str)

    async def test_size_shown_in_result(self, mock_ctx, tmp_path):
        mock_artifact = ArtifactDownload(
            file_path=tmp_path / "paper.pdf",
            content_type="application/pdf",
            size_bytes=12345,
        )
        with patch(
            "zotero_mcp.acquisition.download.ArtifactDownloader.download", new=AsyncMock(return_value=mock_artifact)
        ):
            result = await download_paper_artifact("https://example.com/paper.pdf", ctx=mock_ctx)
        assert "12" in result

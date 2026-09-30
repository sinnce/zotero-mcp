from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zotero_mcp.acquisition.bridge_client import BridgeHealthResult
from zotero_mcp.acquisition.types import AccessLocation, AccessResolution, ArtifactDownload, PipelineError
from zotero_mcp.tools.download_paper_artifact import download_paper_artifact

READY_HEALTH = BridgeHealthResult(True, "http://127.0.0.1:9870", "ready", "Bridge is ready.")
UNAVAILABLE_HEALTH = BridgeHealthResult(False, "http://127.0.0.1:9870", "unavailable", "Bridge is unavailable.")


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


@pytest.mark.asyncio
class TestBridgeFallback:
    def _make_resolution(
        self, requires_session: bool, url: str = "https://proxy.example.com/paper.pdf"
    ) -> AccessResolution:
        loc = AccessLocation(
            url=url,
            access_method="institutional",
            requires_session=requires_session,
            session_kind="libproxy",
        )
        return AccessResolution(
            identifier_type="doi",
            identifier_value="10.1234/test",
            locations=[loc],
            best_location=loc,
        )

    async def test_bridge_fallback_triggered(self, bridge_artifact):
        resolution = self._make_resolution(requires_session=True)
        bridge_result = bridge_artifact("paper.pdf")

        with (
            patch("zotero_mcp.tools.acquire_paper.resolve_access", new=AsyncMock(return_value=resolution)),
            patch("zotero_mcp.tools.acquire_paper.BridgeClient") as MockBridge,
        ):
            mock_instance = MagicMock()
            mock_instance.health = AsyncMock(return_value=READY_HEALTH)
            mock_instance.download = AsyncMock(return_value=bridge_result)
            MockBridge.return_value = mock_instance

            from zotero_mcp.tools.acquire_paper import acquire_paper

            result = await acquire_paper("10.1234/test", session_name="libproxy-snu")

        assert result["status"] == "complete"
        assert result["provenance"]["bridge_session"] == "libproxy-snu"
        assert result["provenance"]["access_source"] == "institutional"
        mock_instance.download.assert_awaited_once()
        call_arg = mock_instance.download.call_args[0][0]
        assert call_arg.session_name == "libproxy-snu"
        assert call_arg.candidate_url == resolution.best_location.url

    async def test_http_path_when_bridge_unavailable(self, tmp_path):
        resolution = self._make_resolution(requires_session=True)
        mock_artifact = ArtifactDownload(
            file_path=tmp_path / "paper.pdf",
            content_type="application/pdf",
            size_bytes=1024,
        )

        with (
            patch("zotero_mcp.tools.acquire_paper.resolve_access", new=AsyncMock(return_value=resolution)),
            patch("zotero_mcp.tools.acquire_paper.BridgeClient") as MockBridge,
            patch("zotero_mcp.tools.acquire_paper.ArtifactDownloader") as MockDownloader,
        ):
            mock_bridge = MagicMock()
            mock_bridge.health = AsyncMock(return_value=UNAVAILABLE_HEALTH)
            MockBridge.return_value = mock_bridge

            mock_dl = MagicMock()
            mock_dl.download = AsyncMock(return_value=mock_artifact)
            MockDownloader.return_value = mock_dl

            from zotero_mcp.tools.acquire_paper import acquire_paper

            result = await acquire_paper("10.1234/test", session_name="libproxy-snu")

        assert result["status"] == "complete"
        assert result["message"] == "Downloaded via HTTP"
        mock_dl.download.assert_called_once()

    async def test_http_path_unaffected(self, tmp_path):
        resolution = self._make_resolution(requires_session=False, url="https://arxiv.org/pdf/2301.00001.pdf")
        mock_artifact = ArtifactDownload(
            file_path=tmp_path / "paper.pdf",
            content_type="application/pdf",
            size_bytes=2048,
        )

        with (
            patch("zotero_mcp.tools.acquire_paper.resolve_access", new=AsyncMock(return_value=resolution)),
            patch("zotero_mcp.tools.acquire_paper.BridgeClient") as MockBridge,
            patch("zotero_mcp.tools.acquire_paper.ArtifactDownloader") as MockDownloader,
        ):
            mock_bridge = MagicMock()
            MockBridge.return_value = mock_bridge

            mock_dl = MagicMock()
            mock_dl.download = AsyncMock(return_value=mock_artifact)
            MockDownloader.return_value = mock_dl

            from zotero_mcp.tools.acquire_paper import acquire_paper

            result = await acquire_paper("2301.00001")

        assert result["status"] == "complete"
        assert result["message"] == "Downloaded via HTTP"
        mock_bridge.health.assert_not_called()
        mock_dl.download.assert_called_once()

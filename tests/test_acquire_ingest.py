from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from zotero_mcp.tools.acquire_paper import acquire_paper
from zotero_mcp.acquisition.types import (
    AccessLocation,
    AccessResolution,
    ArtifactDownload,
    IngestResult,
)


@pytest.fixture
def mock_ctx():
    ctx = MagicMock()
    ctx.info = MagicMock()
    ctx.warning = MagicMock()
    return ctx


def make_resolution(metadata=None, requires_session=False, identifier_type="doi"):
    location = AccessLocation(
        url="https://example.com/paper.pdf",
        access_method="oa",
        requires_session=requires_session,
    )
    return AccessResolution(
        identifier_type=identifier_type,
        identifier_value="10.1234/test",
        locations=[location],
        best_location=location,
        metadata=metadata or {},
    )


def make_download_result(file_path="/tmp/paper.pdf"):
    return ArtifactDownload(
        file_path=Path(file_path),
        content_type="application/pdf",
        size_bytes=1000,
    )


def make_mock_zotero(item_key="NEW_ITEM_KEY"):
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
    zot.create_items.return_value = {
        "successful": {"0": {"key": item_key}},
        "failed": {},
    }
    zot.children.return_value = []
    return zot


@pytest.mark.asyncio
class TestAcquireIngest:
    async def test_auto_ingest_with_metadata(self, mock_ctx):
        metadata = {
            "title": "Test Paper Title",
            "authors": "Smith J, Jones K",
            "abstract": "An abstract.",
            "year": "2024",
            "journal": "Nature",
        }
        resolution = make_resolution(metadata=metadata)
        download = make_download_result()
        ingest_result = IngestResult(item_key="NEW_ITEM_KEY")
        mock_zot = make_mock_zotero()

        with (
            patch("zotero_mcp.tools.acquire_paper.resolve_access", new=AsyncMock(return_value=resolution)),
            patch("zotero_mcp.tools.acquire_paper.ArtifactDownloader") as mock_dl_cls,
            patch("zotero_mcp.tools.acquire_paper._get_write_client", return_value=(mock_zot, mock_zot)),
            patch("zotero_mcp.tools.acquire_paper.ingest_paper", return_value=ingest_result) as mock_ingest,
        ):
            mock_dl_cls.return_value.download = AsyncMock(return_value=download)
            result = await acquire_paper("10.1234/test", auto_ingest=True, ctx=mock_ctx)

        assert result["status"] == "complete"
        assert result["zotero_item_key"] == "NEW_ITEM_KEY"
        mock_ingest.assert_called_once()
        call_kwargs = mock_ingest.call_args[1]
        assert call_kwargs["title"] == "Test Paper Title"
        assert call_kwargs["doi"] == "10.1234/test"
        assert call_kwargs["authors"] == "Smith J, Jones K"
        assert call_kwargs["abstract"] == "An abstract."
        assert call_kwargs["year"] == "2024"
        assert call_kwargs["journal"] == "Nature"

    async def test_no_auto_ingest(self, mock_ctx):
        resolution = make_resolution(metadata={"title": "Test Paper"})
        download = make_download_result()

        with (
            patch("zotero_mcp.tools.acquire_paper.resolve_access", new=AsyncMock(return_value=resolution)),
            patch("zotero_mcp.tools.acquire_paper.ArtifactDownloader") as mock_dl_cls,
            patch("zotero_mcp.tools.acquire_paper.ingest_paper") as mock_ingest,
        ):
            mock_dl_cls.return_value.download = AsyncMock(return_value=download)
            result = await acquire_paper("10.1234/test", auto_ingest=False, ctx=mock_ctx)

        assert result["status"] == "complete"
        assert "file_path" in result
        assert "provenance" in result
        assert "message" in result
        assert "zotero_item_key" not in result
        mock_ingest.assert_not_called()

    async def test_skip_ingest_no_title(self, mock_ctx):
        resolution = make_resolution(metadata={"authors": "Smith J"})
        download = make_download_result()

        with (
            patch("zotero_mcp.tools.acquire_paper.resolve_access", new=AsyncMock(return_value=resolution)),
            patch("zotero_mcp.tools.acquire_paper.ArtifactDownloader") as mock_dl_cls,
            patch("zotero_mcp.tools.acquire_paper.ingest_paper") as mock_ingest,
        ):
            mock_dl_cls.return_value.download = AsyncMock(return_value=download)
            result = await acquire_paper("10.1234/test", auto_ingest=True, ctx=mock_ctx)

        assert result["status"] == "complete"
        assert "file_path" in result
        assert "zotero_item_key" not in result
        mock_ingest.assert_not_called()

    async def test_provenance_format(self, mock_ctx):
        metadata = {"title": "Provenance Test Paper"}
        resolution = make_resolution(metadata=metadata)
        download = make_download_result()
        mock_zot = make_mock_zotero()

        with (
            patch("zotero_mcp.tools.acquire_paper.resolve_access", new=AsyncMock(return_value=resolution)),
            patch("zotero_mcp.tools.acquire_paper.ArtifactDownloader") as mock_dl_cls,
            patch("zotero_mcp.tools.acquire_paper._get_write_client", return_value=(mock_zot, mock_zot)),
        ):
            mock_dl_cls.return_value.download = AsyncMock(return_value=download)
            result = await acquire_paper("10.1234/test", auto_ingest=True, ctx=mock_ctx)

        assert result["status"] == "complete"
        assert "zotero_item_key" in result
        mock_zot.create_items.assert_called_once()
        template = mock_zot.create_items.call_args[0][0][0]
        assert "[Paper Ingest Provenance]" in template.get("extra", "")

    async def test_partial_metadata(self, mock_ctx):
        metadata = {"title": "Partial Metadata Paper"}
        resolution = make_resolution(metadata=metadata)
        download = make_download_result()
        ingest_result = IngestResult(item_key="PARTIAL_KEY")
        mock_zot = make_mock_zotero("PARTIAL_KEY")

        with (
            patch("zotero_mcp.tools.acquire_paper.resolve_access", new=AsyncMock(return_value=resolution)),
            patch("zotero_mcp.tools.acquire_paper.ArtifactDownloader") as mock_dl_cls,
            patch("zotero_mcp.tools.acquire_paper._get_write_client", return_value=(mock_zot, mock_zot)),
            patch("zotero_mcp.tools.acquire_paper.ingest_paper", return_value=ingest_result) as mock_ingest,
        ):
            mock_dl_cls.return_value.download = AsyncMock(return_value=download)
            result = await acquire_paper("10.1234/test", auto_ingest=True, ctx=mock_ctx)

        assert result["status"] == "complete"
        assert result["zotero_item_key"] == "PARTIAL_KEY"
        mock_ingest.assert_called_once()
        call_kwargs = mock_ingest.call_args[1]
        assert call_kwargs["title"] == "Partial Metadata Paper"
        assert call_kwargs.get("authors") is None
        assert call_kwargs.get("abstract") is None
        assert call_kwargs.get("year") is None
        assert call_kwargs.get("journal") is None

    async def test_auto_ingest_default_false(self, mock_ctx):
        resolution = make_resolution(metadata={"title": "Default Test"})
        download = make_download_result()

        with (
            patch("zotero_mcp.tools.acquire_paper.resolve_access", new=AsyncMock(return_value=resolution)),
            patch("zotero_mcp.tools.acquire_paper.ArtifactDownloader") as mock_dl_cls,
            patch("zotero_mcp.tools.acquire_paper.ingest_paper") as mock_ingest,
        ):
            mock_dl_cls.return_value.download = AsyncMock(return_value=download)
            result = await acquire_paper("10.1234/test", ctx=mock_ctx)

        assert result["status"] == "complete"
        assert "zotero_item_key" not in result
        mock_ingest.assert_not_called()

    async def test_auto_ingest_inherits_config_when_omitted(self, mock_ctx):
        resolution = make_resolution(metadata={"title": "Config Enabled"})
        download = make_download_result()
        ingest_result = IngestResult(item_key="CONFIG_KEY")
        mock_zot = make_mock_zotero("CONFIG_KEY")

        with (
            patch("zotero_mcp.tools.acquire_paper.load_acquisition_config") as mock_load_config,
            patch("zotero_mcp.tools.acquire_paper.resolve_access", new=AsyncMock(return_value=resolution)),
            patch("zotero_mcp.tools.acquire_paper.ArtifactDownloader") as mock_dl_cls,
            patch("zotero_mcp.tools.acquire_paper._get_write_client", return_value=(mock_zot, mock_zot)),
            patch("zotero_mcp.tools.acquire_paper.ingest_paper", return_value=ingest_result) as mock_ingest,
        ):
            from zotero_mcp.acquisition.config import AcquisitionConfig

            mock_load_config.return_value = AcquisitionConfig(auto_ingest=True)
            mock_dl_cls.return_value.download = AsyncMock(return_value=download)
            result = await acquire_paper("10.1234/test", ctx=mock_ctx)

        assert result["status"] == "complete"
        assert result["zotero_item_key"] == "CONFIG_KEY"
        mock_ingest.assert_called_once()

    async def test_explicit_false_overrides_config_auto_ingest(self, mock_ctx):
        resolution = make_resolution(metadata={"title": "Config Disabled"})
        download = make_download_result()

        with (
            patch("zotero_mcp.tools.acquire_paper.load_acquisition_config") as mock_load_config,
            patch("zotero_mcp.tools.acquire_paper.resolve_access", new=AsyncMock(return_value=resolution)),
            patch("zotero_mcp.tools.acquire_paper.ArtifactDownloader") as mock_dl_cls,
            patch("zotero_mcp.tools.acquire_paper.ingest_paper") as mock_ingest,
        ):
            from zotero_mcp.acquisition.config import AcquisitionConfig

            mock_load_config.return_value = AcquisitionConfig(auto_ingest=True)
            mock_dl_cls.return_value.download = AsyncMock(return_value=download)
            result = await acquire_paper("10.1234/test", auto_ingest=False, ctx=mock_ctx)

        assert result["status"] == "complete"
        assert "zotero_item_key" not in result
        mock_ingest.assert_not_called()

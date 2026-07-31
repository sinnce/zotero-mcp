import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zotero_mcp.acquisition.bridge_client import BridgeDownloadResult
from zotero_mcp.acquisition.config import AcquisitionConfig
from zotero_mcp.acquisition.types import AccessLocation, AccessResolution
from zotero_mcp.tools.acquire_paper import acquire_paper


def _resolution() -> AccessResolution:
    location = AccessLocation(
        url="https://publisher.example/paper.pdf",
        access_method="institutional",
        requires_session=True,
    )
    return AccessResolution(
        identifier_type="doi",
        identifier_value="10.1000/example",
        locations=[location],
        best_location=location,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bridge_result",
    [
        BridgeDownloadResult(status="auth_required", auth_state="expired", error_code="AUTH_REQUIRED"),
        BridgeDownloadResult(status="failed", auth_state="ready", error_code="DOMAIN_BLOCKED"),
        BridgeDownloadResult(status="failed", auth_state="interactive_required", error_code="CAPTCHA"),
        BridgeDownloadResult(status="failed", auth_state="ready", error_code="CANCELLED"),
    ],
)
async def test_terminal_bridge_results_do_not_start_direct_downloader(bridge_result):
    with (
        patch("zotero_mcp.tools.acquire_paper.load_acquisition_config", return_value=AcquisitionConfig()),
        patch("zotero_mcp.tools.acquire_paper.resolve_access", new=AsyncMock(return_value=_resolution())),
        patch("zotero_mcp.tools.acquire_paper.BridgeClient") as bridge_class,
        patch("zotero_mcp.tools.acquire_paper.ArtifactDownloader") as downloader_class,
    ):
        bridge = MagicMock()
        bridge.is_available = AsyncMock(return_value=True)
        bridge.download = AsyncMock(return_value=bridge_result)
        bridge_class.return_value = bridge

        result = await acquire_paper("10.1000/example", session_name="campus")

    assert result["status"] == "failed"
    assert bridge_result.error_code in result["message"]
    downloader_class.assert_not_called()


@pytest.mark.asyncio
async def test_bridge_cancellation_propagates_without_starting_direct_downloader():
    with (
        patch("zotero_mcp.tools.acquire_paper.load_acquisition_config", return_value=AcquisitionConfig()),
        patch("zotero_mcp.tools.acquire_paper.resolve_access", new=AsyncMock(return_value=_resolution())),
        patch("zotero_mcp.tools.acquire_paper.BridgeClient") as bridge_class,
        patch("zotero_mcp.tools.acquire_paper.ArtifactDownloader") as downloader_class,
    ):
        bridge = MagicMock()
        bridge.is_available = AsyncMock(return_value=True)
        bridge.download = AsyncMock(side_effect=asyncio.CancelledError())
        bridge_class.return_value = bridge

        with pytest.raises(asyncio.CancelledError):
            await acquire_paper("10.1000/example", session_name="campus")

    downloader_class.assert_not_called()

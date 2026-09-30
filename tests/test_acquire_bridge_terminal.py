import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zotero_mcp.acquisition.bridge_client import BridgeDownloadResult, BridgeHealthResult
from zotero_mcp.acquisition.config import AcquisitionConfig, InstitutionalConfig
from zotero_mcp.acquisition.types import AccessLocation, AccessResolution
from zotero_mcp.tools.acquire_paper import acquire_paper

READY_HEALTH = BridgeHealthResult(True, "http://127.0.0.1:9870", "ready", "Bridge is ready.")


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
        BridgeDownloadResult(status="failed", auth_state="unchecked", error_code="UNAUTHORIZED"),
        BridgeDownloadResult(status="failed", auth_state="unchecked", error_code="INVALID_REQUEST"),
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
        bridge.configured = True
        bridge.health = AsyncMock(return_value=READY_HEALTH)
        bridge.download = AsyncMock(return_value=bridge_result)
        bridge_class.return_value = bridge

        result = await acquire_paper("10.1000/example", session_name="campus")

    assert result["status"] == "failed"
    assert result["error_code"] == bridge_result.error_code
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
        bridge.configured = True
        bridge.health = AsyncMock(return_value=READY_HEALTH)
        bridge.download = AsyncMock(side_effect=asyncio.CancelledError())
        bridge_class.return_value = bridge

        with pytest.raises(asyncio.CancelledError):
            await acquire_paper("10.1000/example", session_name="campus")

    downloader_class.assert_not_called()


def _libproxy_resolution() -> AccessResolution:
    location = AccessLocation(
        url="https://libproxy.snu.ac.kr/link.n2s?url=https%3A%2F%2Fdoi.org%2F10.1000%2Fexample",
        access_method="institutional",
        requires_session=True,
        session_kind="libproxy",
    )
    return AccessResolution(
        identifier_type="doi",
        identifier_value="10.1000/example",
        locations=[location],
        best_location=location,
    )


_LIBPROXY_CONFIG = AcquisitionConfig(
    institutional_access=InstitutionalConfig(
        enabled=True,
        provider="libproxy",
        libproxy_base_url="https://libproxy.snu.ac.kr/link.n2s",
    )
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("config", "make_resolution"),
    [
        pytest.param(AcquisitionConfig(), _resolution, id="institutional"),
        pytest.param(_LIBPROXY_CONFIG, _libproxy_resolution, id="direct-then-libproxy"),
    ],
)
async def test_unauthorized_download_is_terminal_without_retry(config, make_resolution):
    unauthorized = BridgeDownloadResult(status="failed", auth_state="unchecked", error_code="UNAUTHORIZED")
    with (
        patch("zotero_mcp.tools.acquire_paper.load_acquisition_config", return_value=config),
        patch("zotero_mcp.tools.acquire_paper.resolve_access", new=AsyncMock(return_value=make_resolution())),
        patch("zotero_mcp.tools.acquire_paper.BridgeClient") as bridge_class,
        patch("zotero_mcp.tools.acquire_paper.ArtifactDownloader") as downloader_class,
    ):
        bridge = MagicMock()
        bridge.configured = True
        bridge.health = AsyncMock(return_value=READY_HEALTH)
        bridge.download = AsyncMock(return_value=unauthorized)
        bridge_class.return_value = bridge

        result = await acquire_paper("10.1000/example", session_name="campus")

    assert result["status"] == "failed"
    assert "[UNAUTHORIZED]" in result["message"]
    # Neither the libproxy route nor the direct downloader is tried with a
    # token the bridge has rejected.
    bridge.download.assert_awaited_once()
    downloader_class.assert_not_called()


@pytest.mark.asyncio
async def test_unauthorized_health_is_terminal_without_download_or_fallback():
    unauthorized = BridgeHealthResult(
        False,
        "http://127.0.0.1:9870",
        "unauthorized",
        "Bridge rejected the configured authentication.",
        error_code="UNAUTHORIZED",
    )
    with (
        patch("zotero_mcp.tools.acquire_paper.load_acquisition_config", return_value=AcquisitionConfig()),
        patch("zotero_mcp.tools.acquire_paper.resolve_access", new=AsyncMock(return_value=_resolution())),
        patch("zotero_mcp.tools.acquire_paper.BridgeClient") as bridge_class,
        patch("zotero_mcp.tools.acquire_paper.ArtifactDownloader") as downloader_class,
    ):
        bridge = MagicMock()
        bridge.configured = True
        bridge.health = AsyncMock(return_value=unauthorized)
        bridge.download = AsyncMock()
        bridge_class.return_value = bridge

        result = await acquire_paper("10.1000/example", session_name="campus")

    assert result["status"] == "failed"
    assert result["error_code"] == "UNAUTHORIZED"
    assert "[UNAUTHORIZED]" in result["message"]
    bridge.health.assert_awaited_once()
    bridge.download.assert_not_called()
    downloader_class.assert_not_called()

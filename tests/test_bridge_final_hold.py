"""Behavioral tests for the r3 final HOLD items.

- URL port validation mirrors the server's WHATWG ``new URL()`` policy.
- ``auto_ingest=True`` returns a caller-owned verified copy, not the staged path.
- Missing or malformed configured bridge auth is terminal, while an
  unconfigured bridge keeps the documented direct-download channel.
"""

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from zotero_mcp.acquisition.bridge_client import (
    BRIDGE_AUTH_INVALID,
    BRIDGE_CONTRACT_VERSION,
    BRIDGE_NOT_CONFIGURED,
    BridgeClient,
    BridgeDownloadRequest,
    BridgeHealthResult,
)
from zotero_mcp.acquisition.config import AcquisitionConfig
from zotero_mcp.acquisition.types import AccessLocation, AccessResolution, ArtifactDownload, IngestResult, PipelineError
from zotero_mcp.tools.acquire_paper import acquire_paper

TOKEN = "test-token-that-is-long-enough-for-the-v2-contract"
REQUEST_ID = "11111111-1111-4111-8111-111111111111"
CONTENT = b"%PDF-1.7 final hold fixture\n"
READY_HEALTH = BridgeHealthResult(True, "http://127.0.0.1:9870", "ready", "Bridge is ready.")
_BRIDGE_ENV = ("ZOTERO_BRIDGE_TOKEN", "BRIDGE_AUTH_TOKEN", "BRIDGE_TOKEN", "BRIDGE_SERVER_URL")

# Port strings that WHATWG `new URL("https://publisher.example:<port>/")`
# (the server's parser in bridge-v2-policy.ts and bridge-v2-contract.ts)
# rejects, probed with Node's URL implementation.
MALFORMED_PORTS = ["65536", "99999", "4294967377", "abc", "80a", "+80", "-1", "8_0", "1e3", "٣", "80:90"]
# Port strings that WHATWG accepts: empty, zero, default, leading zeros, max.
ACCEPTED_PORTS = ["", "0", "443", "8443", "0080", "00000000000000443", "65535"]


def _response(payload, status_code=200):
    response = Mock()
    response.status_code = status_code
    response.headers = {"content-type": "application/json; charset=utf-8"}
    response.json.return_value = payload
    return response


def _request(candidate_url="https://publisher.example/paper.pdf"):
    return BridgeDownloadRequest("10.1000/example", candidate_url, "campus", request_id=REQUEST_ID)


def _success(final_url):
    return {
        "contract_version": BRIDGE_CONTRACT_VERSION,
        "request_id": REQUEST_ID,
        "status": "complete",
        "auth_state": "ready",
        "transfer_id": "33333333-3333-4333-8333-333333333333",
        "file_path": "/tmp/paper.pdf",
        "sha256": "a" * 64,
        "size_bytes": 10,
        "content_type": "application/pdf",
        "final_url": final_url,
        "expires_at": "2026-09-26T12:00:00Z",
        "ack_required": True,
        "error_code": None,
    }


def _download_with_final_url(final_url):
    with patch(
        "zotero_mcp.acquisition.bridge_client.httpx.post",
        return_value=_response(_success(final_url)),
    ):
        return BridgeClient(auth_token=TOKEN).download(_request())


# --- Z1 / Z5: malformed-port URL validation -------------------------------


@pytest.mark.parametrize("port", MALFORMED_PORTS)
def test_success_with_malformed_final_url_port_is_rejected(port):
    result = _download_with_final_url(f"https://publisher.example:{port}/paper.pdf")

    assert result.status == "failed"
    assert result.error_code == "BRIDGE_UNAVAILABLE"
    assert result.file_path is None


@pytest.mark.parametrize("port", ACCEPTED_PORTS)
def test_success_with_whatwg_valid_final_url_port_is_accepted(port):
    final_url = f"https://publisher.example:{port}/paper.pdf"
    result = _download_with_final_url(final_url)

    assert result.status == "complete"
    assert result.final_url == final_url


@pytest.mark.parametrize("port", MALFORMED_PORTS)
def test_candidate_url_with_malformed_port_fails_closed_before_transport(port):
    with patch("zotero_mcp.acquisition.bridge_client.httpx.post") as post:
        result = BridgeClient(auth_token=TOKEN).download(_request(f"https://publisher.example:{port}/paper.pdf"))

    assert result.status == "failed"
    assert result.error_code == "DOMAIN_BLOCKED"
    post.assert_not_called()


@pytest.mark.parametrize("port", ACCEPTED_PORTS)
def test_candidate_url_with_whatwg_valid_port_is_sent(port):
    candidate_url = f"https://publisher.example:{port}/paper.pdf"
    with patch(
        "zotero_mcp.acquisition.bridge_client.httpx.post",
        return_value=_response(_success("https://publisher.example/paper.pdf")),
    ) as post:
        result = BridgeClient(auth_token=TOKEN).download(_request(candidate_url))

    assert result.status == "complete"
    post.assert_called_once()
    assert post.call_args.kwargs["json"]["candidate_url"] == candidate_url


# --- Reg 2: auto_ingest returns a caller-owned verified copy --------------


def _resolution(metadata=None) -> AccessResolution:
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
        metadata={"title": "Final Hold Paper"} if metadata is None else metadata,
    )


async def _acquire_with_ingest(bridge_result, ingest_side_effect):
    zot = MagicMock()
    with (
        patch("zotero_mcp.tools.acquire_paper.load_acquisition_config", return_value=AcquisitionConfig()),
        patch("zotero_mcp.tools.acquire_paper.resolve_access", new=AsyncMock(return_value=_resolution())),
        patch("zotero_mcp.tools.acquire_paper.BridgeClient") as bridge_class,
        patch("zotero_mcp.tools.acquire_paper.ArtifactDownloader") as downloader_class,
        patch("zotero_mcp.tools.acquire_paper._get_write_client", return_value=(zot, zot)),
        patch("zotero_mcp.tools.acquire_paper.ingest_paper", side_effect=ingest_side_effect) as ingest,
    ):
        bridge = MagicMock()
        bridge.health = AsyncMock(return_value=READY_HEALTH)
        bridge.download = AsyncMock(return_value=bridge_result)
        bridge_class.return_value = bridge
        result = await acquire_paper("10.1000/example", session_name="campus", auto_ingest=True)
    return result, ingest, downloader_class


def _assert_caller_owned_verified_copy(returned: Path, bridge_result, caller_root: Path):
    assert str(returned) != bridge_result.file_path
    assert returned.is_relative_to(caller_root)
    assert returned.name == "paper.pdf"
    assert not returned.is_symlink()
    assert os.stat(returned).st_mode & 0o777 == 0o600
    assert os.stat(returned.parent).st_mode & 0o777 == 0o700
    assert returned.read_bytes() == CONTENT


def _rewrite_in_place(path):
    with open(path, "r+b") as handle:
        handle.write(b"X")


def _replace_by_symlink(path):
    decoy = f"{path}.decoy"
    with open(decoy, "wb") as handle:
        handle.write(b"%PDF-1.7 attacker-controlled bytes!\n")
    os.unlink(path)
    os.symlink(decoy, path)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "replace",
    [
        pytest.param(_rewrite_in_place, id="rewrite-in-place"),
        pytest.param(_replace_by_symlink, id="symlink-swap"),
        pytest.param(os.unlink, id="expired"),
    ],
)
async def test_auto_ingest_returns_the_ingested_caller_owned_copy(bridge_artifact, tmp_path, replace):
    caller_root = tmp_path / "caller-tmp"
    bridge_result = bridge_artifact(content=CONTENT)
    ingested = {}

    def _ingest(**kwargs):
        ingested["path"] = kwargs["file_path"]
        return IngestResult(item_key="INGESTED")

    result, ingest, downloader_class = await _acquire_with_ingest(bridge_result, _ingest)

    assert result["status"] == "complete"
    assert result["zotero_item_key"] == "INGESTED"
    returned = Path(result["file_path"])
    # The returned path is the exact verified copy that was ingested, and it
    # outlives the call.
    assert returned == ingested["path"]
    _assert_caller_owned_verified_copy(returned, bridge_result, caller_root)
    # The bridge may replace or expire its staged file afterwards.
    replace(bridge_result.file_path)
    assert returned.read_bytes() == CONTENT
    ingest.assert_called_once()
    downloader_class.assert_not_called()


@pytest.mark.asyncio
async def test_auto_ingest_failure_still_returns_caller_owned_copy(bridge_artifact, tmp_path):
    caller_root = tmp_path / "caller-tmp"
    bridge_result = bridge_artifact(content=CONTENT)

    result, ingest, _ = await _acquire_with_ingest(
        bridge_result, lambda **_kwargs: PipelineError(step="ingest", code="INGEST_FAILED", message="write failed")
    )

    assert result["status"] == "complete"
    assert "zotero_item_key" not in result
    _assert_caller_owned_verified_copy(Path(result["file_path"]), bridge_result, caller_root)
    ingest.assert_called_once()


@pytest.mark.asyncio
async def test_auto_ingest_exception_leaves_no_copy_behind(bridge_artifact, tmp_path):
    caller_root = tmp_path / "caller-tmp"
    bridge_result = bridge_artifact(content=CONTENT)

    def _explode(**_kwargs):
        raise RuntimeError("ingest crashed")

    with pytest.raises(RuntimeError, match="ingest crashed"):
        await _acquire_with_ingest(bridge_result, _explode)

    assert list(caller_root.iterdir()) == []


# --- Reg 3: auth failure is terminal; absent bridge keeps direct channel --


def _clear_bridge_env(monkeypatch):
    for name in _BRIDGE_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(
    ("env", "error_code"),
    [
        pytest.param({}, BRIDGE_NOT_CONFIGURED, id="not-configured"),
        pytest.param({"BRIDGE_AUTH_TOKEN": ""}, BRIDGE_AUTH_INVALID, id="empty-token"),
        pytest.param({"ZOTERO_BRIDGE_TOKEN": "short"}, BRIDGE_AUTH_INVALID, id="malformed-token"),
        pytest.param({"BRIDGE_TOKEN": f"{TOKEN} with spaces"}, BRIDGE_AUTH_INVALID, id="token-with-spaces"),
        pytest.param({"BRIDGE_SERVER_URL": "http://127.0.0.1:9871"}, BRIDGE_AUTH_INVALID, id="url-without-token"),
    ],
)
def test_client_classifies_absent_versus_invalid_auth_without_transport(monkeypatch, env, error_code):
    _clear_bridge_env(monkeypatch)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    client = BridgeClient()
    with (
        patch("zotero_mcp.acquisition.bridge_client.httpx.get") as get,
        patch("zotero_mcp.acquisition.bridge_client.httpx.post") as post,
    ):
        health = client.health()
        result = client.download(_request())

    assert health.available is False
    assert health.error_code == error_code
    assert result.status == "failed"
    assert result.error_code == error_code
    get.assert_not_called()
    post.assert_not_called()


@pytest.mark.parametrize("auth_token", ["", "short", f"{TOKEN}\n"])
def test_explicit_invalid_token_is_configured_but_invalid(monkeypatch, auth_token):
    _clear_bridge_env(monkeypatch)
    client = BridgeClient(auth_token=auth_token)
    with patch("zotero_mcp.acquisition.bridge_client.httpx.get") as get:
        health = client.health()

    assert health.error_code == BRIDGE_AUTH_INVALID
    get.assert_not_called()


def _direct_downloader(tmp_path):
    downloader = MagicMock()
    downloader.download = AsyncMock(
        return_value=ArtifactDownload(file_path=tmp_path / "direct.pdf", content_type="application/pdf", size_bytes=1)
    )
    return downloader


@pytest.mark.asyncio
async def test_unconfigured_bridge_uses_documented_direct_download_channel(monkeypatch, tmp_path):
    _clear_bridge_env(monkeypatch)
    downloader = _direct_downloader(tmp_path)
    with (
        patch("zotero_mcp.tools.acquire_paper.load_acquisition_config", return_value=AcquisitionConfig()),
        patch("zotero_mcp.tools.acquire_paper.resolve_access", new=AsyncMock(return_value=_resolution({}))),
        patch("zotero_mcp.acquisition.bridge_client.httpx.get") as get,
        patch("zotero_mcp.acquisition.bridge_client.httpx.post") as post,
        patch("zotero_mcp.tools.acquire_paper.ArtifactDownloader", return_value=downloader) as downloader_class,
    ):
        result = await acquire_paper("10.1000/example", session_name="campus")

    assert result["status"] == "complete"
    assert result["message"] == "Downloaded via HTTP"
    assert "bridge_session" not in result["provenance"]
    downloader_class.assert_called_once()
    get.assert_not_called()
    post.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "env",
    [
        pytest.param({"BRIDGE_AUTH_TOKEN": ""}, id="empty-token"),
        pytest.param({"ZOTERO_BRIDGE_TOKEN": "short"}, id="malformed-token"),
        pytest.param({"BRIDGE_SERVER_URL": "http://127.0.0.1:9871"}, id="url-without-token"),
    ],
)
async def test_configured_bridge_with_invalid_auth_is_terminal_without_fallback(monkeypatch, env):
    _clear_bridge_env(monkeypatch)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    with (
        patch("zotero_mcp.tools.acquire_paper.load_acquisition_config", return_value=AcquisitionConfig()),
        patch("zotero_mcp.tools.acquire_paper.resolve_access", new=AsyncMock(return_value=_resolution({}))),
        patch("zotero_mcp.acquisition.bridge_client.httpx.get") as get,
        patch("zotero_mcp.acquisition.bridge_client.httpx.post") as post,
        patch("zotero_mcp.tools.acquire_paper.ArtifactDownloader") as downloader_class,
    ):
        result = await acquire_paper("10.1000/example", session_name="campus")

    assert result["status"] == "failed"
    assert result["error_code"] == BRIDGE_AUTH_INVALID
    assert result["message"].startswith(f"[{BRIDGE_AUTH_INVALID}]")
    assert "file_path" not in result
    get.assert_not_called()
    post.assert_not_called()
    downloader_class.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_auth_on_download_is_terminal_without_fallback():
    invalid = MagicMock()
    invalid.health = AsyncMock(return_value=READY_HEALTH)
    invalid.download = AsyncMock(
        return_value=MagicMock(status="failed", error_code=BRIDGE_AUTH_INVALID, file_path=None)
    )
    with (
        patch("zotero_mcp.tools.acquire_paper.load_acquisition_config", return_value=AcquisitionConfig()),
        patch("zotero_mcp.tools.acquire_paper.resolve_access", new=AsyncMock(return_value=_resolution({}))),
        patch("zotero_mcp.tools.acquire_paper.BridgeClient", return_value=invalid),
        patch("zotero_mcp.tools.acquire_paper.ArtifactDownloader") as downloader_class,
    ):
        result = await acquire_paper("10.1000/example", session_name="campus")

    assert result["status"] == "failed"
    assert result["error_code"] == BRIDGE_AUTH_INVALID
    invalid.download.assert_awaited_once()
    downloader_class.assert_not_called()

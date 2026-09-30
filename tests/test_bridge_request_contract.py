import dataclasses
import json
import logging
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import httpx
import pytest

from zotero_mcp.acquisition.bridge_client import (
    BRIDGE_CONTRACT_VERSION,
    BRIDGE_REQUEST_INVALID,
    BRIDGE_SERVICE,
    BridgeClient,
    BridgeDownloadRequest,
)
from zotero_mcp.acquisition.config import AcquisitionConfig
from zotero_mcp.acquisition.types import AccessLocation, AccessResolution, ArtifactDownload
from zotero_mcp.tools.acquire_paper import acquire_paper

TOKEN = "SecretBridgeToken_do-not-leak_0123456789abcdef"
REQUEST_ID = "11111111-1111-4111-8111-111111111111"


def _request(**overrides) -> BridgeDownloadRequest:
    request = BridgeDownloadRequest(
        "10.1000/example",
        "https://publisher.example/paper.pdf",
        "campus",
        request_id=REQUEST_ID,
    )
    return dataclasses.replace(request, **overrides)


def _json_response(status_code: int, payload: object) -> Mock:
    response = Mock()
    response.status_code = status_code
    response.headers = {"content-type": "application/json; charset=utf-8"}
    response.json.return_value = payload
    return response


def _unauthorized() -> dict:
    return {
        "contract_version": BRIDGE_CONTRACT_VERSION,
        "request_id": None,
        "status": "failed",
        "auth_state": "unchecked",
        "error_code": "UNAUTHORIZED",
        "message": "Authentication required.",
    }


def _ready_health() -> dict:
    return {
        "contract_version": BRIDGE_CONTRACT_VERSION,
        "service": BRIDGE_SERVICE,
        "status": "ready",
        "error_code": None,
        "capabilities": {"browser_get_version": True, "fetch_interception": True},
        "sessions": {"configured": 1, "ready": 1},
    }


INVALID_FIELDS = [
    pytest.param({"doi": ""}, id="doi-empty"),
    pytest.param({"doi": "1" * 513}, id="doi-over-512-code-points"),
    pytest.param({"doi": "10.1000/\ud800"}, id="doi-lone-surrogate"),
    pytest.param({"doi": None}, id="doi-none"),
    pytest.param({"doi": 101000}, id="doi-not-string"),
    pytest.param({"session_name": ""}, id="session-empty"),
    pytest.param({"session_name": "Campus"}, id="session-uppercase"),
    pytest.param({"session_name": "-campus"}, id="session-leading-dash"),
    pytest.param({"session_name": ".campus"}, id="session-leading-dot"),
    pytest.param({"session_name": "campus\n"}, id="session-trailing-newline"),
    pytest.param({"session_name": "camp us"}, id="session-space"),
    pytest.param({"session_name": "a" * 129}, id="session-over-128"),
    pytest.param({"session_name": None}, id="session-none"),
    pytest.param({"expected_artifact": "html"}, id="artifact-html"),
    pytest.param({"expected_artifact": "PDF"}, id="artifact-uppercase"),
    pytest.param({"expected_artifact": None}, id="artifact-none"),
    pytest.param({"timeout_ms": 999}, id="timeout-below-min"),
    pytest.param({"timeout_ms": 300001}, id="timeout-above-max"),
    pytest.param({"timeout_ms": 0}, id="timeout-zero"),
    pytest.param({"timeout_ms": -1}, id="timeout-negative"),
    pytest.param({"timeout_ms": True}, id="timeout-bool"),
    pytest.param({"timeout_ms": 60000.0}, id="timeout-float"),
    pytest.param({"timeout_ms": "60000"}, id="timeout-string"),
    pytest.param({"timeout_ms": None}, id="timeout-none"),
    pytest.param({"request_id": "not-a-uuid"}, id="request-id-not-uuid"),
    pytest.param({"request_id": f"{REQUEST_ID}\n"}, id="request-id-trailing-newline"),
    pytest.param({"contract_version": "1.0.0"}, id="contract-legacy"),
    pytest.param({"allowed_domains": "publisher.example"}, id="allowed-domains-string"),
    pytest.param({"allowed_domains": [1]}, id="allowed-domains-non-string"),
]


@pytest.mark.parametrize("overrides", INVALID_FIELDS)
def test_invalid_request_fields_fail_closed_before_transport(overrides):
    with patch("zotero_mcp.acquisition.bridge_client.httpx.post") as post:
        result = BridgeClient(auth_token=TOKEN).download(_request(**overrides))

    assert result.status == "failed"
    assert result.error_code == BRIDGE_REQUEST_INVALID
    assert result.request_id is None
    post.assert_not_called()


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"doi": "1"}, id="doi-min"),
        pytest.param({"doi": "1" * 512}, id="doi-max"),
        # 512 code points but 1024 UTF-16 units: the server counts code points.
        pytest.param({"doi": "\U0001f600" * 512}, id="doi-max-astral"),
        pytest.param({"session_name": "a"}, id="session-min"),
        pytest.param({"session_name": "0" + "a._-" * 31 + "abc"}, id="session-max"),
        pytest.param({"session_name": "libproxy-snu"}, id="session-dash"),
        pytest.param({"timeout_ms": 1000}, id="timeout-min"),
        pytest.param({"timeout_ms": 300000}, id="timeout-max"),
        pytest.param({"allowed_domains": ("publisher.example",)}, id="allowed-domains-tuple"),
    ],
)
def test_boundary_request_fields_are_sent(overrides):
    request = _request(**overrides)
    with patch(
        "zotero_mcp.acquisition.bridge_client.httpx.post",
        return_value=_json_response(503, {**_unauthorized(), "error_code": "CAPABILITY_UNAVAILABLE"}),
    ) as post:
        BridgeClient(auth_token=TOKEN).download(request)

    post.assert_called_once()
    payload = post.call_args.kwargs["json"]
    assert set(payload) == {
        "contract_version",
        "request_id",
        "doi",
        "candidate_url",
        "session_name",
        "expected_artifact",
        "allowed_domains",
        "timeout_ms",
    }
    assert payload["doi"] == request.doi
    assert payload["session_name"] == request.session_name
    assert payload["expected_artifact"] == "pdf"
    assert payload["timeout_ms"] == request.timeout_ms
    assert post.call_args.kwargs["timeout"] == request.timeout_ms / 1000 + 5


def test_health_recognizes_exact_unauthorized_envelope():
    with patch(
        "zotero_mcp.acquisition.bridge_client.httpx.get",
        return_value=_json_response(401, _unauthorized()),
    ):
        health = BridgeClient(auth_token=TOKEN).health()

    assert health.available is False
    assert health.status == "unauthorized"
    assert health.error_code == "UNAUTHORIZED"


@pytest.mark.parametrize(
    ("status_code", "payload"),
    [
        pytest.param(200, _unauthorized(), id="wrong-http-status"),
        pytest.param(401, {**_unauthorized(), "request_id": REQUEST_ID}, id="echoed-request-id"),
        pytest.param(401, {**_unauthorized(), "message": "nope"}, id="message-drift"),
        pytest.param(401, {**_unauthorized(), "extra": True}, id="extra-field"),
    ],
)
def test_health_rejects_unauthorized_envelope_drift(status_code, payload):
    with patch(
        "zotero_mcp.acquisition.bridge_client.httpx.get",
        return_value=_json_response(status_code, payload),
    ):
        health = BridgeClient(auth_token=TOKEN).health()

    assert health.available is False
    assert health.status == "unavailable"
    assert health.error_code is None


def _assert_token_absent(*values: object) -> None:
    for value in values:
        assert TOKEN not in repr(value)


@pytest.mark.parametrize(
    "transport_error",
    [
        pytest.param(httpx.ConnectError(f"connect failed Bearer {TOKEN}"), id="connect-error"),
        pytest.param(httpx.ReadTimeout(f"timed out Bearer {TOKEN}"), id="timeout"),
    ],
)
def test_token_never_appears_in_transport_error_results_or_logs(caplog, transport_error):
    caplog.set_level(logging.DEBUG)
    client = BridgeClient(auth_token=TOKEN)
    with (
        patch("zotero_mcp.acquisition.bridge_client.httpx.get", side_effect=transport_error),
        patch("zotero_mcp.acquisition.bridge_client.httpx.post", side_effect=transport_error),
    ):
        health = client.health()
        result = client.download(_request())

    _assert_token_absent(health, result, client, caplog.text)


def test_token_never_appears_when_bridge_echoes_it(caplog):
    caplog.set_level(logging.DEBUG)
    client = BridgeClient(auth_token=TOKEN)
    echoed = {**_unauthorized(), "message": f"Bearer {TOKEN}"}
    with (
        patch("zotero_mcp.acquisition.bridge_client.httpx.get", return_value=_json_response(401, echoed)),
        patch("zotero_mcp.acquisition.bridge_client.httpx.post", return_value=_json_response(401, echoed)),
    ):
        health = client.health()
        result = client.download(_request())

    assert health.available is False
    assert result.error_code == "BRIDGE_UNAVAILABLE"
    _assert_token_absent(health, result, client, caplog.text)


def test_malformed_token_is_never_sent_or_echoed(caplog):
    caplog.set_level(logging.DEBUG)
    malformed = f"{TOKEN} with spaces"
    client = BridgeClient(auth_token=malformed)
    with (
        patch("zotero_mcp.acquisition.bridge_client.httpx.get") as get,
        patch("zotero_mcp.acquisition.bridge_client.httpx.post") as post,
    ):
        health = client.health()
        result = client.download(_request())

    get.assert_not_called()
    post.assert_not_called()
    _assert_token_absent(health, result, client, caplog.text)


def test_token_is_sent_on_the_wire_but_absent_from_httpx_logs(httpx_mock, caplog):
    caplog.set_level(logging.DEBUG)
    httpx_mock.add_response(
        method="GET",
        url="http://127.0.0.1:9870/bridge/health/ready",
        status_code=401,
        json=_unauthorized(),
    )
    httpx_mock.add_response(
        method="POST",
        url="http://127.0.0.1:9870/bridge/download",
        status_code=401,
        json=_unauthorized(),
    )
    client = BridgeClient(base_url="http://127.0.0.1:9870", auth_token=TOKEN)

    health = client.health()
    result = client.download(_request())

    requests = httpx_mock.get_requests()
    assert [request.headers["authorization"] for request in requests] == [f"Bearer {TOKEN}"] * 2
    assert health.error_code == "UNAUTHORIZED"
    assert result.error_code == "UNAUTHORIZED"
    assert all(TOKEN not in request.content.decode() for request in requests)
    _assert_token_absent(health, result, client, caplog.text)


def _session_resolution() -> AccessResolution:
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
async def test_acquire_rejects_invalid_session_name_without_bridge_download_or_fallback(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("ZOTERO_BRIDGE_TOKEN", TOKEN)
    with (
        patch("zotero_mcp.tools.acquire_paper.load_acquisition_config", return_value=AcquisitionConfig()),
        patch("zotero_mcp.tools.acquire_paper.resolve_access", new=AsyncMock(return_value=_session_resolution())),
        patch(
            "zotero_mcp.acquisition.bridge_client.httpx.get",
            return_value=_json_response(200, _ready_health()),
        ),
        patch("zotero_mcp.acquisition.bridge_client.httpx.post") as post,
        patch("zotero_mcp.tools.acquire_paper.ArtifactDownloader") as downloader_class,
    ):
        result = await acquire_paper("10.1000/example", session_name="Campus Session")

    assert result["status"] == "failed"
    assert f"[{BRIDGE_REQUEST_INVALID}]" in result["message"]
    post.assert_not_called()
    downloader_class.assert_not_called()
    _assert_token_absent(json.dumps(result), caplog.text)


@pytest.mark.asyncio
async def test_acquire_output_and_logs_never_contain_token(monkeypatch, caplog, tmp_path):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("ZOTERO_BRIDGE_TOKEN", TOKEN)
    downloader = MagicMock()
    downloader.download = AsyncMock(
        return_value=ArtifactDownload(file_path=tmp_path / "paper.pdf", content_type="application/pdf", size_bytes=1)
    )
    with (
        patch("zotero_mcp.tools.acquire_paper.load_acquisition_config", return_value=AcquisitionConfig()),
        patch("zotero_mcp.tools.acquire_paper.resolve_access", new=AsyncMock(return_value=_session_resolution())),
        patch(
            "zotero_mcp.acquisition.bridge_client.httpx.get",
            return_value=_json_response(200, _ready_health()),
        ),
        patch(
            "zotero_mcp.acquisition.bridge_client.httpx.post",
            side_effect=httpx.ConnectError(f"refused Authorization: Bearer {TOKEN}"),
        ),
        patch("zotero_mcp.tools.acquire_paper.ArtifactDownloader", return_value=downloader),
    ):
        result = await acquire_paper("10.1000/example", session_name="campus")

    assert result["status"] == "complete"
    _assert_token_absent(json.dumps(result), caplog.text)


@pytest.mark.asyncio
async def test_acquire_stops_when_bridge_rejects_token_at_health(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("ZOTERO_BRIDGE_TOKEN", TOKEN)
    with (
        patch("zotero_mcp.tools.acquire_paper.load_acquisition_config", return_value=AcquisitionConfig()),
        patch("zotero_mcp.tools.acquire_paper.resolve_access", new=AsyncMock(return_value=_session_resolution())),
        patch(
            "zotero_mcp.acquisition.bridge_client.httpx.get",
            return_value=_json_response(401, _unauthorized()),
        ),
        patch("zotero_mcp.acquisition.bridge_client.httpx.post") as post,
        patch("zotero_mcp.tools.acquire_paper.ArtifactDownloader") as downloader_class,
    ):
        result = await acquire_paper("10.1000/example", session_name="campus")

    assert result["status"] == "failed"
    assert result["error_code"] == "UNAUTHORIZED"
    post.assert_not_called()
    downloader_class.assert_not_called()
    _assert_token_absent(json.dumps(result), caplog.text)

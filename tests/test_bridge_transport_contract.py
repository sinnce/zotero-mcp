from unittest.mock import Mock, patch

import httpx
import pytest

from zotero_mcp.acquisition.bridge_client import BridgeClient, BridgeDownloadRequest


def _download_response(status_code: int, payload: dict[str, str | None]) -> Mock:
    response = Mock()
    response.status_code = status_code
    response.json.return_value = payload
    return response


def _download_with_response(response: Mock):
    client = BridgeClient(auth_token="test-token-that-is-long-enough-for-the-contract")
    request = BridgeDownloadRequest("10.1000/example", "https://publisher.example/paper.pdf", "campus")
    with patch("zotero_mcp.acquisition.bridge_client.httpx.post", return_value=response):
        return client.download(request)


@pytest.mark.parametrize(
    "response",
    [
        _download_response(
            500, {"status": "complete", "auth_state": "ready", "error_code": None, "file_path": "/tmp/paper.pdf"}
        ),
        _download_response(403, {"status": "failed", "auth_state": "ready", "error_code": "DOMAIN_BLOCKED"}),
        _download_response(401, {"status": "auth_required", "auth_state": "expired", "error_code": "AUTH_REQUIRED"}),
    ],
)
def test_download_rejects_typed_bodies_from_non_200_responses(response):
    result = _download_with_response(response)

    assert result.status == "failed"
    assert result.auth_state == "missing"
    assert result.error_code == "BRIDGE_UNAVAILABLE"
    assert result.message == "Bridge is unavailable"


def test_download_maps_timeout_exception_to_timeout():
    client = BridgeClient(auth_token="test-token-that-is-long-enough-for-the-contract")
    request = BridgeDownloadRequest("10.1000/example", "https://publisher.example/paper.pdf", "campus")

    with patch("zotero_mcp.acquisition.bridge_client.httpx.post", side_effect=httpx.TimeoutException("timeout")):
        result = client.download(request)

    assert result.error_code == "TIMEOUT"
    assert result.message == "Bridge request failed"


@pytest.mark.parametrize("error", [httpx.ConnectError("connect"), httpx.ReadError("read")])
def test_download_maps_non_timeout_http_errors_to_bridge_unavailable(error):
    client = BridgeClient(auth_token="test-token-that-is-long-enough-for-the-contract")
    request = BridgeDownloadRequest("10.1000/example", "https://publisher.example/paper.pdf", "campus")

    with patch("zotero_mcp.acquisition.bridge_client.httpx.post", side_effect=error):
        result = client.download(request)

    assert result.error_code == "BRIDGE_UNAVAILABLE"
    assert result.message == "Bridge is unavailable"


_FAILED_STATE_MATRIX = {
    "CAPTCHA": {"interactive_required"},
    "DOMAIN_BLOCKED": {"ready", "missing"},
    "HTML_LANDING": {"ready"},
    "NO_PDF": {"ready"},
    "ACCESS_DENIED": {"ready"},
    "TIMEOUT": {"ready"},
    "CANCELLED": {"ready"},
}
_AUTH_STATES = {"ready", "expired", "missing", "interactive_required"}


@pytest.mark.parametrize(
    ("error_code", "auth_state"),
    [
        (error_code, auth_state)
        for error_code, allowed_states in _FAILED_STATE_MATRIX.items()
        for auth_state in _AUTH_STATES - allowed_states
    ],
)
def test_download_rejects_invalid_failed_state_combinations(error_code, auth_state):
    result = _download_with_response(
        _download_response(200, {"status": "failed", "auth_state": auth_state, "error_code": error_code})
    )

    assert result.status == "failed"
    assert result.auth_state == "missing"
    assert result.error_code == "BRIDGE_UNAVAILABLE"

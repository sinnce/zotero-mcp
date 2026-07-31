from unittest.mock import Mock, patch

import pytest

from zotero_mcp.acquisition.bridge_client import BridgeClient, BridgeDownloadRequest, BridgeDownloadResult
from zotero_mcp.tools.acquire_paper import _is_explicit_paywall


def _download(payload):
    response = Mock()
    response.status_code = 200
    response.json.return_value = payload
    client = BridgeClient(auth_token="test-token-that-is-long-enough-for-the-contract")
    request = BridgeDownloadRequest("10.1000/example", "https://publisher.example/paper.pdf", "campus")
    with patch("zotero_mcp.acquisition.bridge_client.httpx.post", return_value=response):
        return client.download(request)


@pytest.mark.parametrize("auth_state", ["missing", "expired", "interactive_required"])
def test_download_accepts_all_auth_required_states(auth_state):
    result = _download(
        {"status": "auth_required", "auth_state": auth_state, "error_code": "AUTH_REQUIRED", "file_path": None}
    )

    assert result.status == "auth_required"
    assert result.auth_state == auth_state
    assert result.error_code == "AUTH_REQUIRED"


@pytest.mark.parametrize("error_code", ["DOMAIN_BLOCKED", "NO_PDF", "CAPTCHA", "CANCELLED"])
def test_download_accepts_all_terminal_failed_codes(error_code):
    result = _download({"status": "failed", "auth_state": "ready", "error_code": error_code, "file_path": None})

    assert result.status == "failed"
    assert result.auth_state == "ready"
    assert result.error_code == error_code


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "complete", "auth_state": "ready", "file_path": "/tmp/paper.pdf"},
        {"status": "complete", "auth_state": "ready", "error_code": "AUTH_REQUIRED", "file_path": "/tmp/paper.pdf"},
        {"status": "complete", "auth_state": "ready", "error_code": None},
        {"status": "complete", "auth_state": "ready", "error_code": None, "file_path": None},
        {"status": "complete", "auth_state": "ready", "error_code": None, "file_path": ""},
        {"status": "complete", "auth_state": "ready", "error_code": None, "file_path": 1},
    ],
)
def test_download_rejects_incomplete_complete_response(payload):
    result = _download(payload)

    assert result.status == "failed"
    assert result.auth_state == "missing"
    assert result.error_code == "BRIDGE_UNAVAILABLE"


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "complete", "auth_state": "expired", "error_code": None, "file_path": "/tmp/paper.pdf"},
        {"status": "auth_required", "auth_state": "ready", "error_code": "AUTH_REQUIRED", "file_path": None},
        {"status": "auth_required", "auth_state": "interactive_required", "error_code": "CAPTCHA", "file_path": None},
        {"status": "auth_required", "auth_state": "expired", "error_code": "AUTH_REQUIRED"},
        {
            "status": "auth_required",
            "auth_state": "expired",
            "error_code": "AUTH_REQUIRED",
            "file_path": "/tmp/paper.pdf",
        },
        {"status": "failed", "auth_state": "expired", "error_code": "NO_PDF", "file_path": None},
        {"status": "failed", "auth_state": "ready", "error_code": "AUTH_REQUIRED", "file_path": None},
        {"status": "failed", "auth_state": "ready", "error_code": "NO_PDF", "file_path": "/tmp/paper.pdf"},
    ],
)
def test_download_rejects_invalid_status_combinations(payload):
    result = _download(payload)

    assert result.status == "failed"
    assert result.auth_state == "missing"
    assert result.error_code == "BRIDGE_UNAVAILABLE"


@pytest.mark.parametrize("error_code", ["NO_PDF", "CAPTCHA", "CANCELLED"])
def test_terminal_bridge_errors_do_not_trigger_libproxy_fallback(error_code):
    result = BridgeDownloadResult(status="failed", auth_state="ready", error_code=error_code)

    assert _is_explicit_paywall(result) is False

from unittest.mock import Mock, patch

import pytest

from zotero_mcp.acquisition.bridge_client import BridgeClient, BridgeDownloadRequest

TOKEN = "test-token-that-is-long-enough-for-the-v2-contract"
REQUEST_ID = "11111111-1111-4111-8111-111111111111"


def _response(payload, status_code=200):
    response = Mock()
    response.status_code = status_code
    response.headers = {"content-type": "application/json; charset=utf-8"}
    response.json.return_value = payload
    return response


def _request():
    return BridgeDownloadRequest(
        "10.1000/example",
        "https://publisher.example/paper.pdf",
        "campus",
        request_id=REQUEST_ID,
    )


def _valid_success():
    return {
        "contract_version": "2.0.0",
        "request_id": REQUEST_ID,
        "status": "complete",
        "auth_state": "ready",
        "transfer_id": "33333333-3333-4333-8333-333333333333",
        "file_path": "/tmp/paper.pdf",
        "sha256": "a" * 64,
        "size_bytes": 10,
        "content_type": "application/pdf",
        "final_url": "https://publisher.example/paper.pdf",
        "expires_at": "2026-09-26T12:00:00Z",
        "ack_required": True,
        "error_code": None,
    }


def _error(code, status, auth_state, message, request_id=REQUEST_ID):
    return {
        "contract_version": "2.0.0",
        "request_id": request_id,
        "status": status,
        "auth_state": auth_state,
        "error_code": code,
        "message": message,
    }


def _download(payload, status_code=200):
    with patch(
        "zotero_mcp.acquisition.bridge_client.httpx.post",
        return_value=_response(payload, status_code),
    ):
        return BridgeClient(auth_token=TOKEN).download(_request())


@pytest.mark.parametrize(
    ("payload", "status", "auth_state", "error_code", "http_status"),
    [
        (
            _error("AUTH_REQUIRED", "auth_required", "expired", "Browser authentication required."),
            "auth_required",
            "expired",
            "AUTH_REQUIRED",
            428,
        ),
        (
            _error("AUTH_MISSING", "auth_required", "missing", "Browser authentication is missing."),
            "auth_required",
            "missing",
            "AUTH_MISSING",
            428,
        ),
        (
            _error("CAPTCHA", "auth_required", "interactive_required", "Browser challenge requires interaction."),
            "auth_required",
            "interactive_required",
            "CAPTCHA",
            428,
        ),
        (
            _error("DOMAIN_BLOCKED", "failed", "unchecked", "Destination domain is blocked."),
            "failed",
            "unchecked",
            "DOMAIN_BLOCKED",
            422,
        ),
        (
            _error("CAPABILITY_UNAVAILABLE", "failed", "unchecked", "Browser interception is unavailable."),
            "failed",
            "unchecked",
            "CAPABILITY_UNAVAILABLE",
            503,
        ),
    ],
)
def test_download_accepts_exact_v2_error_envelopes(payload, status, auth_state, error_code, http_status):
    result = _download(payload, http_status)
    assert (result.status, result.auth_state, result.error_code) == (status, auth_state, error_code)
    assert result.contract_version == "2.0.0"


@pytest.mark.parametrize(
    "payload",
    [
        {**_valid_success(), "extra": True},
        {**_valid_success(), "sha256": "A" * 64},
        {**_valid_success(), "size_bytes": True},
        {**_valid_success(), "final_url": "http://publisher.example/paper.pdf"},
        {**_valid_success(), "request_id": "22222222-2222-4222-8222-222222222222"},
        {**_valid_success(), "ack_required": False},
    ],
)
def test_download_rejects_invalid_v2_success_payloads(payload):
    result = _download(payload)
    assert result.error_code == "BRIDGE_UNAVAILABLE"


@pytest.mark.parametrize(
    ("payload", "status_code"),
    [
        (_valid_success(), 500),
        (_error("DOMAIN_BLOCKED", "failed", "unchecked", "Destination domain is blocked."), 200),
        ({**_error("DOMAIN_BLOCKED", "failed", "unchecked", "Destination domain is blocked."), "message": "leak"}, 422),
    ],
)
def test_download_rejects_http_or_message_contract_drift(payload, status_code):
    result = _download(payload, status_code)
    assert result.error_code == "BRIDGE_UNAVAILABLE"


def test_unauthorized_error_must_not_echo_request_id():
    payload = _error("UNAUTHORIZED", "failed", "unchecked", "Authentication required.", request_id=REQUEST_ID)
    result = _download(payload, 401)
    assert result.error_code == "BRIDGE_UNAVAILABLE"

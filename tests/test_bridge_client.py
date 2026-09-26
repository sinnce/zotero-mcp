from unittest.mock import Mock, patch

import httpx
import pytest

from zotero_mcp.acquisition.bridge_client import (
    BRIDGE_CONTRACT_VERSION,
    BRIDGE_SERVICE,
    BridgeClient,
    BridgeDownloadRequest,
)

TOKEN = "test-token-that-is-long-enough-for-the-v2-contract"
REQUEST_ID = "11111111-1111-4111-8111-111111111111"
TRANSFER_ID = "33333333-3333-4333-8333-333333333333"


def _response(status_code: int, payload: object, *, content_type: str = "application/json") -> Mock:
    response = Mock()
    response.status_code = status_code
    response.headers = {"content-type": content_type}
    response.json.return_value = payload
    return response


def _request(**kwargs) -> BridgeDownloadRequest:
    return BridgeDownloadRequest(
        "10.1000/example",
        "https://publisher.example/paper.pdf",
        "campus",
        request_id=REQUEST_ID,
        **kwargs,
    )


def _success(**overrides) -> dict:
    value = {
        "contract_version": BRIDGE_CONTRACT_VERSION,
        "request_id": REQUEST_ID,
        "status": "complete",
        "auth_state": "ready",
        "transfer_id": TRANSFER_ID,
        "file_path": "/tmp/paper.pdf",
        "sha256": "a" * 64,
        "size_bytes": 123,
        "content_type": "application/pdf",
        "final_url": "https://publisher.example/paper.pdf",
        "expires_at": "2026-09-26T12:00:00Z",
        "ack_required": True,
        "error_code": None,
    }
    value.update(overrides)
    return value


def _error(code: str, *, status: str = "failed", auth_state: str = "unchecked", request_id=REQUEST_ID) -> dict:
    return {
        "contract_version": BRIDGE_CONTRACT_VERSION,
        "request_id": request_id,
        "status": status,
        "auth_state": auth_state,
        "error_code": code,
        "message": {
            "AUTH_EXPIRED": "Browser authentication expired.",
            "DOMAIN_BLOCKED": "Destination domain is blocked.",
            "CAPABILITY_UNAVAILABLE": "Browser interception is unavailable.",
        }.get(code, "Internal bridge failure."),
    }


def _health(status: str = "ready", *, context_fetch: bool = False) -> dict:
    capabilities = {"browser_get_version": status == "ready", "fetch_interception": status == "ready"}
    if context_fetch:
        capabilities["fetch_interception"] = False
        capabilities["browser_context_fetch"] = status == "ready"
    if status == "ready":
        return {
            "contract_version": BRIDGE_CONTRACT_VERSION,
            "service": BRIDGE_SERVICE,
            "status": "ready",
            "error_code": None,
            "capabilities": capabilities,
            "sessions": {"configured": 1, "ready": 1},
        }
    capabilities = {"browser_get_version": False, "fetch_interception": False}
    if context_fetch:
        capabilities["browser_context_fetch"] = False
    return {
        "contract_version": BRIDGE_CONTRACT_VERSION,
        "service": BRIDGE_SERVICE,
        "status": "not_ready",
        "error_code": "CAPABILITY_UNAVAILABLE",
        "capabilities": capabilities,
        "sessions": {"configured": 1, "ready": 0},
    }


def test_missing_token_fails_closed_without_http_request(monkeypatch):
    monkeypatch.delenv("ZOTERO_BRIDGE_TOKEN", raising=False)
    monkeypatch.delenv("BRIDGE_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("BRIDGE_TOKEN", raising=False)
    client = BridgeClient()
    with patch("zotero_mcp.acquisition.bridge_client.httpx.get") as get, patch(
        "zotero_mcp.acquisition.bridge_client.httpx.post"
    ) as post:
        assert client.is_available() is False
        assert client.download(_request()).error_code == "BRIDGE_UNAVAILABLE"
    get.assert_not_called()
    post.assert_not_called()


def test_download_emits_v2_identity_auth_and_allowlist():
    client = BridgeClient(auth_token=TOKEN)
    with patch(
        "zotero_mcp.acquisition.bridge_client.httpx.post",
        return_value=_response(200, _success()),
    ) as post:
        result = client.download(_request())

    assert result.status == "complete"
    assert result.request_id == REQUEST_ID
    assert result.transfer_id == TRANSFER_ID
    assert result.sha256 == "a" * 64
    assert result.size_bytes == 123
    assert result.ack_required is True
    assert post.call_args.kwargs["headers"] == {
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/json",
        "Content-Type": "application/json; charset=utf-8",
    }
    payload = post.call_args.kwargs["json"]
    assert payload["contract_version"] == BRIDGE_CONTRACT_VERSION
    assert payload["request_id"] == REQUEST_ID
    assert payload["allowed_domains"] == ["publisher.example"]


def test_download_derives_nested_domains_and_bounded_extras(monkeypatch):
    monkeypatch.setenv(
        "BRIDGE_ALLOWED_DOMAINS",
        "Example.COM, example.com, bad domain, " + ",".join(f"extra{i}.example" for i in range(40)),
    )
    request = BridgeDownloadRequest(
        "10.1000/example",
        "https://libproxy.example/link?url=https%3A%2F%2Fpublisher.example%2Fpaper%3Furl%3D"
        "https%253A%252F%252Fcdn.example%252Fpaper.pdf",
        "campus",
        request_id=REQUEST_ID,
    )
    with patch(
        "zotero_mcp.acquisition.bridge_client.httpx.post",
        return_value=_response(200, _success(final_url="https://publisher.example/paper.pdf")),
    ) as post:
        BridgeClient(auth_token=TOKEN).download(request)
    domains = post.call_args.kwargs["json"]["allowed_domains"]
    assert domains[:3] == ["libproxy.example", "publisher.example", "cdn.example"]
    assert domains.count("example.com") == 1
    assert "bad domain" not in domains
    assert len(domains) == 32


def test_explicit_allowlist_is_authoritative_and_serialized():
    request = BridgeDownloadRequest(
        "10.1000/example",
        "https://publisher.example/paper.pdf",
        "campus",
        request_id=REQUEST_ID,
        allowed_domains=["publisher.example"],
    )
    with patch(
        "zotero_mcp.acquisition.bridge_client.httpx.post",
        return_value=_response(503, _error("CAPABILITY_UNAVAILABLE")),
    ) as post:
        BridgeClient(auth_token=TOKEN).download(request)
    assert post.call_args.kwargs["json"]["allowed_domains"] == ["publisher.example"]


@pytest.mark.parametrize(
    "candidate_url",
    [
        "http://publisher.example/paper.pdf",
        "https://127.0.0.1/paper.pdf",
        "https://publisher.example./paper.pdf",
        "https://user:password@publisher.example/paper.pdf",
        "https://publisher.example/paper.pdf#fragment",
    ],
)
def test_candidate_url_policy_fails_closed_before_transport(candidate_url):
    request = BridgeDownloadRequest("10.1000/example", candidate_url, "campus", request_id=REQUEST_ID)
    with patch("zotero_mcp.acquisition.bridge_client.httpx.post") as post:
        result = BridgeClient(auth_token=TOKEN).download(request)
    assert result.error_code == "DOMAIN_BLOCKED"
    post.assert_not_called()


def test_missing_request_id_is_generated_and_sent_as_uuid():
    request = BridgeDownloadRequest("10.1000/example", "https://publisher.example/paper.pdf", "campus")
    with patch(
        "zotero_mcp.acquisition.bridge_client.httpx.post",
        return_value=_response(503, _error("CAPABILITY_UNAVAILABLE", request_id=None)),
    ) as post:
        BridgeClient(auth_token=TOKEN).download(request)
    request_id = post.call_args.kwargs["json"]["request_id"]
    assert isinstance(request_id, str) and len(request_id) == 36


def test_download_preserves_typed_v2_error_from_non_200_response():
    with patch(
        "zotero_mcp.acquisition.bridge_client.httpx.post",
        return_value=_response(428, _error("AUTH_EXPIRED", status="auth_required", auth_state="expired")),
    ):
        result = BridgeClient(auth_token=TOKEN).download(_request())
    assert result.status == "auth_required"
    assert result.auth_state == "expired"
    assert result.error_code == "AUTH_EXPIRED"


def test_download_rejects_malformed_or_incomplete_success():
    for payload in (
        _success(file_path=None),
        _success(sha256="A" * 64),
        _success(size_bytes=True),
        _success(final_url="https://other.example/paper.pdf"),
        {**_success(), "extra": True},
    ):
        with patch(
            "zotero_mcp.acquisition.bridge_client.httpx.post",
            return_value=_response(200, payload),
        ):
            result = BridgeClient(auth_token=TOKEN).download(_request())
        assert result.error_code == "BRIDGE_UNAVAILABLE"


def test_download_maps_timeout_and_transport_errors():
    with patch(
        "zotero_mcp.acquisition.bridge_client.httpx.post",
        side_effect=httpx.TimeoutException("timeout"),
    ):
        assert BridgeClient(auth_token=TOKEN).download(_request()).error_code == "TIMEOUT"
    with patch(
        "zotero_mcp.acquisition.bridge_client.httpx.post",
        side_effect=httpx.ConnectError("connect"),
    ):
        assert BridgeClient(auth_token=TOKEN).download(_request()).error_code == "BRIDGE_UNAVAILABLE"


def test_ready_health_requires_v2_capabilities_and_auth():
    client = BridgeClient(auth_token=TOKEN)
    with patch(
        "zotero_mcp.acquisition.bridge_client.httpx.get",
        return_value=_response(200, _health(), content_type="application/json; charset=utf-8"),
    ) as get:
        health = client.health()
    assert health.available is True
    assert health.status == "ready"
    assert health.ready_sessions == 1
    assert get.call_args.kwargs["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert get.call_args.args[0].endswith("/bridge/health/ready")


def test_health_rejects_unknown_capability_fields():
    payload = _health()
    payload["capabilities"]["browser_context_fetch"] = True
    with patch(
        "zotero_mcp.acquisition.bridge_client.httpx.get",
        return_value=_response(200, payload),
    ):
        assert BridgeClient(auth_token=TOKEN).is_available() is False


def test_not_ready_health_is_not_available_even_with_valid_json():
    with patch(
        "zotero_mcp.acquisition.bridge_client.httpx.get",
        return_value=_response(503, _health("not_ready")),
    ):
        health = BridgeClient(auth_token=TOKEN).health()
    assert health.available is False
    assert health.status == "not_ready"
    assert health.error_code == "CAPABILITY_UNAVAILABLE"


@pytest.mark.parametrize(
    "response",
    [
        _response(200, _health("ready"), content_type="text/plain"),
        _response(200, {**_health("ready"), "service": "other"}),
        _response(200, {**_health("ready"), "sessions": {"configured": 1, "ready": 2}}),
        _response(503, {**_health("not_ready"), "capabilities": {"browser_get_version": True, "fetch_interception": False}}),
    ],
)
def test_health_fails_closed_for_invalid_readiness(response):
    with patch("zotero_mcp.acquisition.bridge_client.httpx.get", return_value=response):
        assert BridgeClient(auth_token=TOKEN).is_available() is False

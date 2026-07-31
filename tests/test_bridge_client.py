from unittest.mock import Mock, patch

import httpx
import pytest

from zotero_mcp.acquisition.bridge_client import BridgeClient, BridgeDownloadRequest


def _response(status_code: int, payload: dict[str, str] | None = None) -> Mock:
    response = Mock()
    response.status_code = status_code
    response.json.return_value = payload or {}
    response.raise_for_status.side_effect = (
        httpx.HTTPStatusError("status", request=Mock(), response=response) if status_code >= 400 else None
    )
    return response


def test_missing_token_fails_closed_without_http_request(monkeypatch):
    # Given: bridge authentication is not configured.
    monkeypatch.delenv("BRIDGE_AUTH_TOKEN", raising=False)
    client = BridgeClient()
    request = BridgeDownloadRequest("10.1000/example", "https://publisher.example/paper.pdf", "campus")

    # When: availability and a download are requested.
    with (
        patch("zotero_mcp.acquisition.bridge_client.httpx.get") as get,
        patch("zotero_mcp.acquisition.bridge_client.httpx.post") as post,
    ):
        available = client.is_available()
        result = client.download(request)

    # Then: no request is sent and the bridge is unavailable.
    assert available is False
    assert result.status == "failed"
    assert result.error_code == "BRIDGE_UNAVAILABLE"
    get.assert_not_called()
    post.assert_not_called()


def test_health_and_download_use_identical_bearer_auth_headers():
    # Given: a configured bridge client.
    token = "test-token-that-is-long-enough-for-the-contract"
    client = BridgeClient(auth_token=token)
    request = BridgeDownloadRequest("10.1000/example", "https://publisher.example/paper.pdf", "campus")

    # When: the client probes health and downloads.
    with (
        patch("zotero_mcp.acquisition.bridge_client.httpx.get", return_value=_response(200)) as get,
        patch(
            "zotero_mcp.acquisition.bridge_client.httpx.post",
            return_value=_response(200, {"status": "complete", "auth_state": "ready"}),
        ) as post,
    ):
        assert client.is_available() is True
        result = client.download(request)

    # Then: both endpoints receive the same bearer credentials.
    assert result.status == "complete"
    assert result.auth_state == "ready"
    assert get.call_args.kwargs["headers"] == {"Authorization": f"Bearer {token}"}
    assert post.call_args.kwargs["headers"] == {"Authorization": f"Bearer {token}"}


def test_download_maps_unauthorized_without_exposing_token():
    # Given: a configured client whose bridge rejects its credentials.
    token = "test-token-that-must-not-appear-in-errors-or-logs"
    client = BridgeClient(auth_token=token)
    request = BridgeDownloadRequest("10.1000/example", "https://publisher.example/paper.pdf", "campus")

    # When: the bridge returns 401.
    with patch("zotero_mcp.acquisition.bridge_client.httpx.post", return_value=_response(401)):
        result = client.download(request)

    # Then: the authorization result is explicit and redacted.
    assert result.error_code == "AUTH_REQUIRED"
    assert result.auth_state == "missing"
    assert token not in (result.message or "")


def test_download_preserves_domain_blocked_for_acquisition_fallback():
    # Given: a direct DOI attempt denied by the bridge allowlist.
    client = BridgeClient(auth_token="test-token-that-is-long-enough-for-the-contract")
    request = BridgeDownloadRequest("10.1000/example", "https://doi.org/10.1000/example", "campus")

    # When: the bridge responds with its DOMAIN_BLOCKED contract error.
    with patch(
        "zotero_mcp.acquisition.bridge_client.httpx.post",
        return_value=_response(403, {"status": "failed", "error_code": "DOMAIN_BLOCKED", "auth_state": "ready"}),
    ):
        result = client.download(request)

    # Then: acquisition can distinguish the fallback-eligible result.
    assert result.error_code == "DOMAIN_BLOCKED"
    assert result.auth_state == "ready"


def test_download_preserves_valid_auth_required_response():
    client = BridgeClient(auth_token="test-token-that-is-long-enough-for-the-contract")
    request = BridgeDownloadRequest("10.1000/example", "https://publisher.example/paper.pdf", "campus")

    with patch(
        "zotero_mcp.acquisition.bridge_client.httpx.post",
        return_value=_response(
            403, {"status": "auth_required", "auth_state": "expired", "error_code": "AUTH_REQUIRED"}
        ),
    ):
        result = client.download(request)

    assert result.status == "auth_required"
    assert result.auth_state == "expired"
    assert result.error_code == "AUTH_REQUIRED"


def test_download_fails_stably_when_response_json_is_malformed():
    response = _response(200)
    response.json.side_effect = ValueError("malformed JSON")
    client = BridgeClient(auth_token="test-token-that-is-long-enough-for-the-contract")
    request = BridgeDownloadRequest("10.1000/example", "https://publisher.example/paper.pdf", "campus")

    with patch("zotero_mcp.acquisition.bridge_client.httpx.post", return_value=response):
        result = client.download(request)

    assert result.status == "failed"
    assert result.auth_state == "missing"
    assert result.error_code == "BRIDGE_UNAVAILABLE"
    assert result.message == "Bridge authentication is unavailable"


@pytest.mark.parametrize("payload", [[], "not an object", 1])
def test_download_fails_stably_when_response_json_is_not_an_object(payload):
    client = BridgeClient(auth_token="test-token-that-is-long-enough-for-the-contract")
    request = BridgeDownloadRequest("10.1000/example", "https://publisher.example/paper.pdf", "campus")

    with patch("zotero_mcp.acquisition.bridge_client.httpx.post", return_value=_response(200, payload)):
        result = client.download(request)

    assert result.status == "failed"
    assert result.auth_state == "missing"
    assert result.error_code == "BRIDGE_UNAVAILABLE"


@pytest.mark.parametrize(
    "payload",
    [
        {"auth_state": "ready", "error_code": "AUTH_REQUIRED"},
        {"status": "failed", "error_code": "AUTH_REQUIRED"},
        {"status": "failed", "auth_state": "ready"},
    ],
)
def test_download_fails_stably_when_error_response_fields_are_missing(payload):
    client = BridgeClient(auth_token="test-token-that-is-long-enough-for-the-contract")
    request = BridgeDownloadRequest("10.1000/example", "https://publisher.example/paper.pdf", "campus")

    with patch("zotero_mcp.acquisition.bridge_client.httpx.post", return_value=_response(403, payload)):
        result = client.download(request)

    assert result.status == "failed"
    assert result.auth_state == "missing"
    assert result.error_code == "BRIDGE_UNAVAILABLE"


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "unknown", "auth_state": "ready", "error_code": "AUTH_REQUIRED"},
        {"status": "failed", "auth_state": "unknown", "error_code": "AUTH_REQUIRED"},
        {"status": "failed", "auth_state": "ready", "error_code": "UNKNOWN"},
    ],
)
def test_download_fails_stably_when_response_enums_are_invalid(payload):
    client = BridgeClient(auth_token="test-token-that-is-long-enough-for-the-contract")
    request = BridgeDownloadRequest("10.1000/example", "https://publisher.example/paper.pdf", "campus")

    with patch("zotero_mcp.acquisition.bridge_client.httpx.post", return_value=_response(200, payload)):
        result = client.download(request)

    assert result.status == "failed"
    assert result.auth_state == "missing"
    assert result.error_code == "BRIDGE_UNAVAILABLE"


def test_download_payload_derives_nested_candidate_domains_and_bounded_extras(monkeypatch):
    # Given: a LibProxy URL with two nested targets and malformed or excessive environment extras.
    monkeypatch.setenv(
        "BRIDGE_ALLOWED_DOMAINS",
        ", LIBPROXY.example, Example.COM, example.com ,bad domain, " + ",".join(f"extra{i}.example" for i in range(40)),
    )
    candidate_url = (
        "https://LIBPROXY.example/link?url=https%3A%2F%2FPUBLISHER.example%2Fpaper%3Furl%3D"
        "https%253A%252F%252FCDN.example%252Fpaper.pdf"
    )
    client = BridgeClient(auth_token="test-token-that-is-long-enough-for-the-contract")
    request = BridgeDownloadRequest("10.1000/example", candidate_url, "campus")

    # When: the request is serialized for the bridge.
    with patch(
        "zotero_mcp.acquisition.bridge_client.httpx.post",
        return_value=_response(200, {"status": "complete", "auth_state": "ready"}),
    ) as post:
        client.download(request)

    # Then: candidate hosts lead the allowlist, malformed extras are omitted, and the list is bounded.
    domains = post.call_args.kwargs["json"]["allowed_domains"]
    assert domains[:3] == ["libproxy.example", "publisher.example", "cdn.example"]
    assert "example.com" in domains
    assert domains.count("libproxy.example") == 1
    assert domains.count("example.com") == 1
    assert "bad domain" not in domains
    assert len(domains) == 32

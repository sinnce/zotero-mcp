"""Rule-by-rule parity of the client bridge policy with the 2.0.0 server.

Every row names the code the server returns (``server``) and the code the
client produces (``client``). They are equal except where a row is marked
as a client-only guard or as the documented IDNA divergence; in those rows
the client refuses something the server would accept, never the reverse.
The ``server`` column was checked against the server code itself
(``bridge-v2-policy.ts``, ``bridge-v2-contract.ts``, ``bridge-server.ts``).
"""

from unittest.mock import Mock, patch

import httpx
import pytest

from zotero_mcp.acquisition.bridge_client import (
    BRIDGE_HEALTH_INVALID,
    BRIDGE_RESPONSE_INVALID,
    BridgeClient,
    BridgeDownloadRequest,
)

TOKEN = "ParityToken_0123456789abcdefghijklmnopqrstuvwxyz"
REQUEST_ID = "11111111-1111-4111-8111-111111111111"
ACCEPT = "ACCEPT"
REJECT = "REJECT"
LONG_HOST = "a" * 63 + "." + "b" * 63 + "." + "c" * 63 + "." + "d" * 61


def _payload(**overrides) -> dict:
    payload = {
        "contract_version": "2.0.0",
        "request_id": REQUEST_ID,
        "doi": "10.1000/example",
        "candidate_url": "https://publisher.example/paper.pdf",
        "session_name": "campus",
        "expected_artifact": "pdf",
        "allowed_domains": ["publisher.example"],
        "timeout_ms": 60000,
    }
    payload.update(overrides)
    return payload


def _row(rule, server, client=None, **overrides):
    return pytest.param(_payload(**overrides), server, client or server, id=rule)


# (payload, server code, client code). ACCEPT: the request passes every check
# before the session registry and is sent.
REQUEST_ROWS = [
    # classifyVersion (bridge-server.ts), before the schema.
    _row("version-legacy-three-part", "LEGACY_DISABLED", contract_version="1.0.0"),
    _row("version-legacy-two-part", "LEGACY_DISABLED", contract_version="1.2"),
    _row("version-one-part", "UNSUPPORTED_CONTRACT_VERSION", contract_version="1"),
    _row("version-four-part", "UNSUPPORTED_CONTRACT_VERSION", contract_version="1.0.0.0"),
    _row("version-legacy-trailing-newline", "UNSUPPORTED_CONTRACT_VERSION", contract_version="1.0\n"),
    _row("version-future", "UNSUPPORTED_CONTRACT_VERSION", contract_version="2.0.1"),
    _row("version-padded", "UNSUPPORTED_CONTRACT_VERSION", contract_version="2.0.0 "),
    # bridgeDownloadRequestSchema.
    _row("request-id-uppercase", ACCEPT, request_id=REQUEST_ID.upper()),
    _row("request-id-nil", ACCEPT, request_id="00000000-0000-0000-0000-000000000000"),
    _row("request-id-max", ACCEPT, request_id="ffffffff-ffff-ffff-ffff-ffffffffffff"),
    _row("request-id-version-9", "INVALID_REQUEST", request_id="11111111-1111-9111-8111-111111111111"),
    _row("request-id-variant-c", "INVALID_REQUEST", request_id="11111111-1111-4111-c111-111111111111"),
    _row("doi-512-astral-code-points", ACCEPT, doi="\U0001f600" * 512),
    _row("doi-513-code-points", "INVALID_REQUEST", doi="1" * 513),
    _row("candidate-url-2048", ACCEPT, candidate_url="https://publisher.example/" + "a" * 2022),
    _row("candidate-url-2049", "INVALID_REQUEST", candidate_url="https://publisher.example/" + "a" * 2023),
    _row("candidate-url-tab", "INVALID_REQUEST", candidate_url="https://publisher.example/a\tb"),
    _row("candidate-url-non-ascii", "INVALID_REQUEST", candidate_url="https://publisher.example/caf\u00e9"),
    _row("session-underscore-dot-dash", ACCEPT, session_name="a_b.c-d"),
    _row("session-uppercase", "INVALID_REQUEST", session_name="Campus"),
    _row("timeout-integral-float", ACCEPT, timeout_ms=60000.0),
    _row("timeout-exponent-float", ACCEPT, timeout_ms=1e3),
    _row("timeout-huge-int", "INVALID_REQUEST", timeout_ms=10**30),
    _row(
        "allowed-domains-33",
        "INVALID_REQUEST",
        allowed_domains=["publisher.example"] + [f"d{i}.example" for i in range(32)],
    ),
    _row("allowed-domain-254", "INVALID_REQUEST", allowed_domains=["publisher.example", LONG_HOST + "d"]),
    # validateBridgeDestination (bridge-v2-policy.ts:82-117), in order.
    _row("doi-leading-space", "INVALID_REQUEST", doi=" 10.1000/example"),
    _row("doi-trailing-tab", "INVALID_REQUEST", doi="10.1000/example\t"),
    _row("doi-trailing-newline", "INVALID_REQUEST", doi="10.1000/example\n"),
    _row("doi-leading-nbsp", "INVALID_REQUEST", doi="\u00a010.1000/example"),
    _row("doi-leading-bom", "INVALID_REQUEST", doi="\ufeff10.1000/example"),
    _row("doi-trailing-ideographic-space", "INVALID_REQUEST", doi="10.1000/example\u3000"),
    _row("doi-trailing-line-separator", "INVALID_REQUEST", doi="10.1000/example\u2028"),
    _row("doi-only-whitespace", "INVALID_REQUEST", doi=" "),
    _row("doi-inner-space", ACCEPT, doi="10.1000/ex ample"),
    _row("doi-leading-file-separator", ACCEPT, doi="\x1c10.1000/example"),
    _row("doi-trailing-unit-separator", ACCEPT, doi="10.1000/example\x1f"),
    _row("doi-leading-next-line", ACCEPT, doi="\u008510.1000/example"),
    _row("doi-leading-mongolian-vowel-separator", ACCEPT, doi="\u180e10.1000/example"),
    _row("doi-leading-zero-width-space", ACCEPT, doi="\u200b10.1000/example"),
    _row("lone-percent", "INVALID_REQUEST", candidate_url="https://publisher.example/a%zz"),
    _row("lone-percent-at-end", "INVALID_REQUEST", candidate_url="https://publisher.example/a%"),
    _row("valid-percent-escape", ACCEPT, candidate_url="https://publisher.example/a%20b"),
    _row("parse-no-scheme", "INVALID_REQUEST", candidate_url="publisher.example/paper.pdf"),
    _row("parse-empty-host", "INVALID_REQUEST", candidate_url="https://"),
    _row("parse-port-out-of-range", "INVALID_REQUEST", candidate_url="https://publisher.example:65536/paper.pdf"),
    _row("parse-port-non-digit", "INVALID_REQUEST", candidate_url="https://publisher.example:44x/paper.pdf"),
    _row("parse-host-ends-in-bad-number", "INVALID_REQUEST", candidate_url="https://publisher.0x/paper.pdf"),
    _row("parse-host-forbidden-code-point", "INVALID_REQUEST", candidate_url="https://pub<lisher.example/paper.pdf"),
    _row("parse-percent-decoded-nul", "INVALID_REQUEST", candidate_url="https://publisher%00.example/paper.pdf"),
    _row("parse-percent-decoded-invalid-utf8", "INVALID_REQUEST", candidate_url="https://%ff.example/paper.pdf"),
    _row("parse-bad-ipv6", "INVALID_REQUEST", candidate_url="https://[::1/paper.pdf"),
    _row("parse-special-without-slashes", ACCEPT, candidate_url="https:publisher.example/paper.pdf"),
    _row("parse-backslashes", ACCEPT, candidate_url="https:\\\\publisher.example\\paper.pdf"),
    _row("parse-uppercase-scheme-and-host", ACCEPT, candidate_url="HTTPS://PUBLISHER.EXAMPLE/paper.pdf"),
    _row("parse-default-port", ACCEPT, candidate_url="https://publisher.example:443/paper.pdf"),
    _row("parse-other-port", ACCEPT, candidate_url="https://publisher.example:8443/paper.pdf"),
    _row("parse-percent-encoded-ascii-host", ACCEPT, candidate_url="https://publisher%2Eexample/paper.pdf"),
    _row("scheme-http", "URL_SCHEME_BLOCKED", candidate_url="http://publisher.example/paper.pdf"),
    _row("scheme-ftp", "URL_SCHEME_BLOCKED", candidate_url="ftp://publisher.example/paper.pdf"),
    _row("scheme-file", "URL_SCHEME_BLOCKED", candidate_url="file:///etc/passwd"),
    _row("scheme-javascript", "URL_SCHEME_BLOCKED", candidate_url="javascript:alert(1)"),
    _row("scheme-ws", "URL_SCHEME_BLOCKED", candidate_url="wss://publisher.example/paper.pdf"),
    _row("userinfo-username", "INVALID_REQUEST", candidate_url="https://user@publisher.example/paper.pdf"),
    _row("userinfo-password", "INVALID_REQUEST", candidate_url="https://:pw@publisher.example/paper.pdf"),
    _row("fragment", "INVALID_REQUEST", candidate_url="https://publisher.example/paper.pdf#page=2"),
    _row("fragment-empty", ACCEPT, candidate_url="https://publisher.example/paper.pdf#"),
    _row("host-ipv4", "DOMAIN_BLOCKED", candidate_url="https://127.0.0.1/paper.pdf"),
    _row("host-ipv4-hex", "DOMAIN_BLOCKED", candidate_url="https://0x7f.1/paper.pdf"),
    _row("host-ipv6", "DOMAIN_BLOCKED", candidate_url="https://[::1]/paper.pdf"),
    _row("host-single-label", "DOMAIN_BLOCKED", candidate_url="https://localhost/paper.pdf"),
    _row("host-trailing-dot", "DOMAIN_BLOCKED", candidate_url="https://publisher.example./paper.pdf"),
    _row("host-underscore", "DOMAIN_BLOCKED", candidate_url="https://pub_lisher.example/paper.pdf"),
    _row("host-label-64", "DOMAIN_BLOCKED", candidate_url=f"https://{'a' * 64}.example/paper.pdf"),
    _row("host-idna-not-allowlisted", "DOMAIN_BLOCKED", candidate_url="https://caf%C3%A9.example/paper.pdf"),
    _row("host-punycode-empty", "INVALID_REQUEST", candidate_url="https://xn--.example/paper.pdf"),
    _row("host-punycode-all-ascii", "INVALID_REQUEST", candidate_url="https://xn--abc-.example/paper.pdf"),
    # Documented divergence: hosts that need UTS #46 are refused by the client.
    _row(
        "idna-divergence-punycode-host",
        ACCEPT,
        "DOMAIN_BLOCKED",
        candidate_url="https://xn--caf-dma.example/paper.pdf",
        allowed_domains=["xn--caf-dma.example"],
    ),
    _row(
        "idna-divergence-percent-encoded-host",
        ACCEPT,
        "DOMAIN_BLOCKED",
        candidate_url="https://caf%C3%A9.example/paper.pdf",
        allowed_domains=["xn--caf-dma.example"],
    ),
    # normalizeAllowedDomains (bridge-v2-policy.ts:34-60).
    _row("allowlist-entry-ip", "DOMAIN_BLOCKED", allowed_domains=["publisher.example", "127.0.0.1"]),
    _row("allowlist-entry-single-label", "DOMAIN_BLOCKED", allowed_domains=["publisher.example", "example"]),
    _row("allowlist-entry-trailing-dot", "DOMAIN_BLOCKED", allowed_domains=["publisher.example."]),
    _row("allowlist-entry-wildcard", "DOMAIN_BLOCKED", allowed_domains=["*.publisher.example"]),
    _row("allowlist-entry-port", "DOMAIN_BLOCKED", allowed_domains=["publisher.example:443"]),
    _row("allowlist-entry-path", "DOMAIN_BLOCKED", allowed_domains=["publisher.example/"]),
    _row("allowlist-entry-underscore", "DOMAIN_BLOCKED", allowed_domains=["publisher.example", "a_b.example"]),
    _row("allowlist-entry-ipv4-leading-zero", ACCEPT, allowed_domains=["publisher.example", "01.2.3.4"]),
    _row("allowlist-entry-three-numbers", ACCEPT, allowed_domains=["publisher.example", "1.2.3"]),
    _row("allowlist-entry-ends-in-number", ACCEPT, allowed_domains=["publisher.example", "example.123"]),
    _row("allowlist-entry-ends-in-hex", ACCEPT, allowed_domains=["publisher.example", "a.0x1f"]),
    _row("allowlist-entry-ends-in-letter-digit", ACCEPT, allowed_domains=["publisher.example", "example.a1"]),
    _row("allowlist-entry-253", ACCEPT, allowed_domains=["publisher.example", LONG_HOST]),
    _row("allowlist-entry-punycode", ACCEPT, allowed_domains=["publisher.example", "xn--caf-dma.example"]),
    _row("allowlist-entry-unchecked-punycode", ACCEPT, allowed_domains=["publisher.example", "xn--zz.example"]),
    _row("allowlist-entry-bare-ace-prefix", "DOMAIN_BLOCKED", allowed_domains=["publisher.example", "xn--.example"]),
    _row("allowlist-entry-label-64", "DOMAIN_BLOCKED", allowed_domains=["publisher.example", "a" * 64 + ".example"]),
    _row("allowlist-entry-empty-label", "DOMAIN_BLOCKED", allowed_domains=["publisher.example", "a..example"]),
    _row("allowlist-entry-leading-hyphen", "DOMAIN_BLOCKED", allowed_domains=["publisher.example", "-a.example"]),
    _row(
        "allowlist-duplicate-case-insensitive",
        "DOMAIN_BLOCKED",
        allowed_domains=["publisher.example", "PUBLISHER.example"],
    ),
    _row("allowlist-mixed-case-entry", ACCEPT, allowed_domains=["Publisher.EXAMPLE"]),
    # Host match against the normalized allowlist.
    _row("match-subdomain", ACCEPT, candidate_url="https://cdn.publisher.example/paper.pdf"),
    _row("match-suffix-without-dot", "DOMAIN_BLOCKED", candidate_url="https://evilpublisher.example/paper.pdf"),
    _row("match-parent-of-allowed", "DOMAIN_BLOCKED", allowed_domains=["cdn.publisher.example"]),
    _row("match-second-entry", ACCEPT, allowed_domains=["other.example", "publisher.example"]),
    # Check order: the first failing check decides the code.
    _row("order-version-before-schema", "LEGACY_DISABLED", contract_version="1.0.0", session_name="Bad"),
    _row("order-version-before-destination", "UNSUPPORTED_CONTRACT_VERSION", contract_version="3.0.0", doi=" x"),
    _row(
        "order-schema-before-destination",
        "INVALID_REQUEST",
        session_name="Bad",
        candidate_url="http://publisher.example/paper.pdf",
    ),
    _row(
        "order-empty-allowlist-before-scheme",
        "INVALID_REQUEST",
        allowed_domains=[],
        candidate_url="http://publisher.example/paper.pdf",
    ),
    _row("order-doi-trim-before-scheme", "INVALID_REQUEST", doi="10.1/x ", candidate_url="http://publisher.example/"),
    _row("order-lone-percent-before-scheme", "INVALID_REQUEST", candidate_url="http://publisher.example/%zz"),
    _row("order-parse-before-scheme", "INVALID_REQUEST", candidate_url="ftp://publisher.example:99999/"),
    _row("order-scheme-before-userinfo", "URL_SCHEME_BLOCKED", candidate_url="http://user@publisher.example/"),
    _row("order-scheme-before-fragment", "URL_SCHEME_BLOCKED", candidate_url="http://publisher.example/#x"),
    _row("order-scheme-before-host", "URL_SCHEME_BLOCKED", candidate_url="http://127.0.0.1/"),
    _row("order-userinfo-before-host", "INVALID_REQUEST", candidate_url="https://user@127.0.0.1/"),
    _row(
        "order-fragment-before-allowlist",
        "INVALID_REQUEST",
        candidate_url="https://publisher.example/#x",
        allowed_domains=["127.0.0.1"],
    ),
    _row(
        "order-host-before-allowlist",
        "DOMAIN_BLOCKED",
        candidate_url="https://localhost/",
        allowed_domains=["publisher.example"],
    ),
]


def _accepted_response() -> Mock:
    response = Mock()
    response.status_code = 503
    response.headers = {"content-type": "application/json; charset=utf-8"}
    response.json.return_value = {
        "contract_version": "2.0.0",
        "request_id": REQUEST_ID,
        "status": "failed",
        "auth_state": "unchecked",
        "error_code": "CAPABILITY_UNAVAILABLE",
        "message": "Browser interception is unavailable.",
    }
    return response


def _request_from(payload: dict) -> BridgeDownloadRequest:
    return BridgeDownloadRequest(
        doi=payload["doi"],
        candidate_url=payload["candidate_url"],
        session_name=payload["session_name"],
        expected_artifact=payload["expected_artifact"],
        timeout_ms=payload["timeout_ms"],
        request_id=payload["request_id"],
        allowed_domains=payload["allowed_domains"],
        contract_version=payload["contract_version"],
    )


@pytest.mark.parametrize(("payload", "server_code", "client_code"), REQUEST_ROWS)
def test_request_policy_matches_server(payload, server_code, client_code):
    with patch("zotero_mcp.acquisition.bridge_client.httpx.post", return_value=_accepted_response()) as post:
        result = BridgeClient(auth_token=TOKEN).download(_request_from(payload))

    if client_code == ACCEPT:
        post.assert_called_once()
        assert post.call_args.kwargs["json"]["doi"] == payload["doi"]
        assert post.call_args.kwargs["json"]["candidate_url"] == payload["candidate_url"]
    else:
        post.assert_not_called()
        assert result.status == "failed"
        assert result.error_code == client_code
        assert result.request_id is None


def test_request_rows_only_diverge_toward_refusal():
    for row in REQUEST_ROWS:
        _, server_code, client_code = row.values
        if server_code != client_code:
            assert server_code == ACCEPT and client_code == "DOMAIN_BLOCKED", row.id
            assert row.id.startswith("idna-divergence"), row.id


def _success(**overrides) -> dict:
    payload = {
        "contract_version": "2.0.0",
        "request_id": REQUEST_ID,
        "status": "complete",
        "auth_state": "ready",
        "transfer_id": "33333333-3333-4333-8333-333333333333",
        "file_path": "/srv/bridge/paper.pdf",
        "sha256": "a" * 64,
        "size_bytes": 10,
        "content_type": "application/pdf",
        "final_url": "https://publisher.example/paper.pdf",
        "expires_at": "2026-09-26T12:00:00Z",
        "ack_required": True,
        "error_code": None,
    }
    payload.update(overrides)
    return payload


def _srow(rule, server, client=None, **overrides):
    return pytest.param(_success(**overrides), server, client or server, id=rule)


# bridgeDownloadSuccessSchema (bridge-v2-contract.ts:141-166, 255-287).
SUCCESS_ROWS = [
    _srow("valid", ACCEPT),
    _srow("extra-key", REJECT, message="done"),
    _srow("contract-version-legacy", REJECT, contract_version="1.0.0"),
    _srow("status-failed", REJECT, status="failed"),
    _srow("auth-state-expired", REJECT, auth_state="expired"),
    _srow("transfer-id-uppercase", ACCEPT, transfer_id="33333333-3333-4333-8333-333333333333".upper()),
    _srow("transfer-id-not-uuid", REJECT, transfer_id="transfer"),
    _srow("file-path-root", ACCEPT, file_path="/"),
    _srow("file-path-relative", REJECT, file_path="paper.pdf"),
    _srow("file-path-nul", REJECT, file_path="/srv/pa\0per.pdf"),
    _srow("file-path-4096-code-points", ACCEPT, file_path="/" + "\U0001f600" * 4095),
    _srow("file-path-4097-code-points", REJECT, file_path="/" + "a" * 4096),
    _srow("sha256-uppercase", REJECT, sha256="A" * 64),
    _srow("sha256-short", REJECT, sha256="a" * 63),
    _srow("size-min", ACCEPT, size_bytes=1),
    _srow("size-zero", REJECT, size_bytes=0),
    _srow("size-max", ACCEPT, size_bytes=104857600),
    _srow("size-over-max", REJECT, size_bytes=104857601),
    _srow("size-fractional", REJECT, size_bytes=10.5),
    _srow("size-bool", REJECT, size_bytes=True),
    _srow("content-type-html", REJECT, content_type="text/html"),
    _srow("final-url-uppercase-scheme", ACCEPT, final_url="HTTPS://publisher.example/paper.pdf"),
    _srow("final-url-port", ACCEPT, final_url="https://publisher.example:8443/paper.pdf"),
    _srow("final-url-bad-port", REJECT, final_url="https://publisher.example:99999/paper.pdf"),
    _srow("final-url-subdomain", ACCEPT, final_url="https://cdn.publisher.example/paper.pdf"),
    _srow("final-url-http", REJECT, final_url="http://publisher.example/paper.pdf"),
    _srow("final-url-userinfo", REJECT, final_url="https://user@publisher.example/paper.pdf"),
    _srow("final-url-fragment", REJECT, final_url="https://publisher.example/paper.pdf#x"),
    _srow("final-url-empty-fragment", ACCEPT, final_url="https://publisher.example/paper.pdf#"),
    _srow("final-url-lone-percent", REJECT, final_url="https://publisher.example/a%zz"),
    _srow("final-url-ipv4", REJECT, final_url="https://127.0.0.1/paper.pdf"),
    _srow("final-url-ipv6", REJECT, final_url="https://[::1]/paper.pdf"),
    _srow("final-url-trailing-dot", REJECT, final_url="https://publisher.example./paper.pdf"),
    _srow("final-url-single-label", REJECT, final_url="https://localhost/paper.pdf"),
    _srow("final-url-2049", REJECT, final_url="https://publisher.example/" + "a" * 2023),
    # Client-only: the server only reports URLs it navigated within the
    # request allowlist; the client checks that as well.
    _srow("client-only-final-url-outside-allowlist", ACCEPT, REJECT, final_url="https://other.example/paper.pdf"),
    # Documented divergence: hosts that need UTS #46.
    _srow("idna-divergence-final-url", ACCEPT, REJECT, final_url="https://xn--caf-dma.publisher.example/paper.pdf"),
    _srow("expires-at-without-seconds", ACCEPT, expires_at="2026-09-26T12:00Z"),
    _srow("expires-at-fraction", ACCEPT, expires_at="2026-09-26T12:00:00.123456Z"),
    _srow("expires-at-offset", REJECT, expires_at="2026-09-26T12:00:00+00:00"),
    _srow("expires-at-lowercase-z", REJECT, expires_at="2026-09-26T12:00:00z"),
    _srow("expires-at-no-zone", REJECT, expires_at="2026-09-26T12:00:00"),
    _srow("expires-at-leap-day", ACCEPT, expires_at="2028-02-29T00:00:00Z"),
    _srow("expires-at-leap-day-2000", ACCEPT, expires_at="2000-02-29T00:00:00Z"),
    _srow("expires-at-non-leap-day", REJECT, expires_at="2026-02-29T00:00:00Z"),
    _srow("expires-at-non-leap-day-1900", REJECT, expires_at="1900-02-29T00:00:00Z"),
    _srow("expires-at-hour-24", REJECT, expires_at="2026-09-26T24:00:00Z"),
    _srow("expires-at-non-ascii-digit", REJECT, expires_at="2026-09-26T1\u0662:00:00Z"),
    _srow("ack-required-false", REJECT, ack_required=False),
    _srow("error-code-set", REJECT, error_code="NO_PDF"),
    # Client-only: the response must echo the request's request_id.
    _srow("client-only-request-id-echo", ACCEPT, REJECT, request_id="22222222-2222-4222-8222-222222222222"),
]


@pytest.mark.parametrize(("payload", "server_verdict", "client_verdict"), SUCCESS_ROWS)
def test_success_schema_matches_server(payload, server_verdict, client_verdict):
    response = Mock()
    response.status_code = 200
    response.headers = {"content-type": "application/json; charset=utf-8"}
    response.json.return_value = payload
    request = BridgeDownloadRequest(
        "10.1000/example", "https://publisher.example/paper.pdf", "campus", request_id=REQUEST_ID
    )
    with patch("zotero_mcp.acquisition.bridge_client.httpx.post", return_value=response):
        result = BridgeClient(auth_token=TOKEN).download(request)

    if client_verdict == ACCEPT:
        assert result.status == "complete"
        assert result.final_url == payload["final_url"]
    else:
        assert result.status == "failed"
        assert result.error_code == BRIDGE_RESPONSE_INVALID
        assert result.file_path is None


def _error(code="NO_PDF", message="No PDF was found.", **overrides) -> dict:
    payload = {
        "contract_version": "2.0.0",
        "request_id": REQUEST_ID,
        "status": "failed",
        "auth_state": "unchecked",
        "error_code": code,
        "message": message,
    }
    payload.update(overrides)
    return payload


def _erow(rule, body, http_status, server, client_code):
    return pytest.param(body, http_status, server, client_code, id=rule)


UNAUTHORIZED_MESSAGE = "Authentication required."

# bridgeErrorResponseSchema (bridge-v2-contract.ts:60-134, 168-188); the HTTP
# status per code comes from the same catalog.
ERROR_ROWS = [
    _erow("no-pdf", _error(), 404, ACCEPT, "NO_PDF"),
    _erow("null-request-id", _error(request_id=None), 404, ACCEPT, "NO_PDF"),
    _erow(
        "session-busy",
        _error("SESSION_BUSY", "Browser session is busy.", auth_state="busy"),
        409,
        ACCEPT,
        "SESSION_BUSY",
    ),
    _erow("unauthorized", _error("UNAUTHORIZED", UNAUTHORIZED_MESSAGE, request_id=None), 401, ACCEPT, "UNAUTHORIZED"),
    _erow(
        "unauthorized-with-request-id",
        _error("UNAUTHORIZED", UNAUTHORIZED_MESSAGE),
        401,
        REJECT,
        BRIDGE_RESPONSE_INVALID,
    ),
    _erow("extra-key", _error(detail="x"), 404, REJECT, BRIDGE_RESPONSE_INVALID),
    _erow("unknown-code", _error("NOT_A_CODE"), 404, REJECT, BRIDGE_RESPONSE_INVALID),
    _erow("wrong-version", _error(contract_version="1.0.0"), 404, REJECT, BRIDGE_RESPONSE_INVALID),
    _erow("wrong-status", _error(status="auth_required"), 404, REJECT, BRIDGE_RESPONSE_INVALID),
    _erow("wrong-auth-state", _error(auth_state="busy"), 404, REJECT, BRIDGE_RESPONSE_INVALID),
    _erow("wrong-message", _error(message="No PDF."), 404, REJECT, BRIDGE_RESPONSE_INVALID),
    _erow("request-id-not-uuid", _error(request_id="x"), 404, REJECT, BRIDGE_RESPONSE_INVALID),
    # Client-only: a non-null request_id must echo the request's, and the
    # HTTP status must be the catalog status for the code.
    _erow(
        "client-only-request-id-echo",
        _error(request_id="22222222-2222-4222-8222-222222222222"),
        404,
        ACCEPT,
        BRIDGE_RESPONSE_INVALID,
    ),
    _erow("client-only-http-status", _error(), 500, ACCEPT, BRIDGE_RESPONSE_INVALID),
]


@pytest.mark.parametrize(("body", "http_status", "server_verdict", "client_code"), ERROR_ROWS)
def test_error_schema_matches_server(body, http_status, server_verdict, client_code):
    response = Mock()
    response.status_code = http_status
    response.headers = {"content-type": "application/json; charset=utf-8"}
    response.json.return_value = body
    request = BridgeDownloadRequest(
        "10.1000/example", "https://publisher.example/paper.pdf", "campus", request_id=REQUEST_ID
    )
    with patch("zotero_mcp.acquisition.bridge_client.httpx.post", return_value=response):
        result = BridgeClient(auth_token=TOKEN).download(request)

    assert result.status != "complete"
    assert result.error_code == client_code
    if client_code != BRIDGE_RESPONSE_INVALID:
        assert result.message == body["message"]


def _health(status="ready", **overrides) -> dict:
    ready = status == "ready"
    payload = {
        "contract_version": "2.0.0",
        "service": "pkb-browser-bridge",
        "status": status,
        "error_code": None if ready else "CAPABILITY_UNAVAILABLE",
        "capabilities": {"browser_get_version": ready, "fetch_interception": ready},
        "sessions": {"configured": 1, "ready": 1 if ready else 0},
    }
    payload.update(overrides)
    return payload


def _hrow(rule, body, http_status, server, client_status, client_code=None):
    return pytest.param(body, http_status, server, client_status, client_code, id=rule)


# bridgeHealthSchema (bridge-v2-contract.ts:195-228) as served by
# readinessResponse (bridge-server.ts): ready is 200, not_ready is 503.
HEALTH_ROWS = [
    _hrow("ready", _health(), 200, ACCEPT, "ready"),
    _hrow("not-ready", _health("not_ready"), 503, ACCEPT, "not_ready", "CAPABILITY_UNAVAILABLE"),
    _hrow(
        "not-ready-with-capabilities",
        _health("not_ready", capabilities={"browser_get_version": True, "fetch_interception": True}),
        503,
        REJECT,
        "invalid",
        BRIDGE_HEALTH_INVALID,
    ),
    _hrow(
        "ready-with-error-code",
        _health(error_code="CAPABILITY_UNAVAILABLE"),
        200,
        REJECT,
        "invalid",
        BRIDGE_HEALTH_INVALID,
    ),
    _hrow("extra-key", _health(uptime=5), 200, REJECT, "invalid", BRIDGE_HEALTH_INVALID),
    _hrow("wrong-service", _health(service="other"), 200, REJECT, "invalid", BRIDGE_HEALTH_INVALID),
    _hrow("wrong-version", _health(contract_version="1.0.0"), 200, REJECT, "invalid", BRIDGE_HEALTH_INVALID),
    _hrow(
        "sessions-over-1000",
        _health(sessions={"configured": 1001, "ready": 1}),
        200,
        REJECT,
        "invalid",
        BRIDGE_HEALTH_INVALID,
    ),
    _hrow(
        "sessions-bool",
        _health(sessions={"configured": True, "ready": 1}),
        200,
        REJECT,
        "invalid",
        BRIDGE_HEALTH_INVALID,
    ),
    _hrow(
        "capabilities-extra-key",
        _health(capabilities={"browser_get_version": True, "fetch_interception": True, "x": True}),
        200,
        REJECT,
        "invalid",
        BRIDGE_HEALTH_INVALID,
    ),
    # Client-only: "alive" is the liveness body (/bridge/health/live); the
    # readiness route never serves it.
    _hrow(
        "client-only-alive-on-readiness-route",
        {"contract_version": "2.0.0", "service": "pkb-browser-bridge", "status": "alive"},
        200,
        ACCEPT,
        "invalid",
        BRIDGE_HEALTH_INVALID,
    ),
    # Client-only: readinessResponse never reports more ready than configured
    # sessions, or a ready body with a non-200 status.
    _hrow(
        "client-only-ready-over-configured",
        _health(sessions={"configured": 1, "ready": 2}),
        200,
        ACCEPT,
        "invalid",
        BRIDGE_HEALTH_INVALID,
    ),
    _hrow("client-only-ready-with-503", _health(), 503, ACCEPT, "invalid", BRIDGE_HEALTH_INVALID),
]


@pytest.mark.parametrize(("body", "http_status", "server_verdict", "client_status", "client_code"), HEALTH_ROWS)
def test_health_schema_matches_server(body, http_status, server_verdict, client_status, client_code):
    response = Mock()
    response.status_code = http_status
    response.headers = {"content-type": "application/json; charset=utf-8"}
    response.json.return_value = body
    with patch("zotero_mcp.acquisition.bridge_client.httpx.get", return_value=response):
        health = BridgeClient(auth_token=TOKEN).health()

    assert health.status == client_status
    assert health.error_code == client_code
    assert health.available is (client_status == "ready")


def test_health_transport_error_is_unreachable():
    with patch("zotero_mcp.acquisition.bridge_client.httpx.get", side_effect=httpx.ConnectError("refused")):
        health = BridgeClient(auth_token=TOKEN).health()

    assert health.available is False
    assert health.status == "unreachable"
    assert health.error_code == "BRIDGE_UNREACHABLE"

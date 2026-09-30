"""The client against verdicts executed by the real 2.0.0 bridge server.

``tests/fixtures/bridge_server_oracle.json`` holds the response the server
code (``bridge-server.ts``, ``bridge-v2-contract.ts``, ``bridge-v2-policy.ts``
under Bun) returned for each case in ``bridge_server_oracle.py``. These tests
require the client to reproduce each response: the whole error envelope for a
request it refuses locally, the request it sends otherwise, the token
normalization and the request-schema defaults. The ``bridge_server`` tests
re-run the server and require the recording to be current.
"""

import os
from pathlib import Path
from unittest.mock import Mock, patch

import bridge_server_oracle as oracle
import pytest
import test_bridge_policy_parity as parity

from zotero_mcp.acquisition.bridge_client import (
    BRIDGE_AUTH_INVALID,
    BridgeClient,
    BridgeDownloadRequest,
)

FIXTURE = oracle.load_fixture()
REQUEST_ROWS = oracle.request_rows()
_ENVELOPE_KEYS = ("contract_version", "request_id", "status", "auth_state", "error_code", "message")
_TOKEN_ENV = ("ZOTERO_BRIDGE_TOKEN", "BRIDGE_AUTH_TOKEN", "BRIDGE_TOKEN", "BRIDGE_SERVER_URL")


def _accepted_response(request_id: str) -> Mock:
    response = parity._accepted_response()
    response.json.return_value = {**response.json.return_value, "request_id": request_id}
    return response


def _download(payload: dict, auth_token: str = oracle.VALID_TOKEN):
    request_id = payload["request_id"]
    response = _accepted_response(request_id if isinstance(request_id, str) else parity.REQUEST_ID)
    with patch("zotero_mcp.acquisition.bridge_client.httpx.post", return_value=response) as post:
        result = BridgeClient(auth_token=auth_token).download(parity._request_from(payload))
    return result, post


def test_recording_covers_exactly_the_current_cases():
    assert FIXTURE["schema"] == oracle.SCHEMA
    assert set(FIXTURE["requests"]) == {row_id for row_id, _ in REQUEST_ROWS}
    assert set(FIXTURE["tokens"]) == {case_id for case_id, _ in oracle.TOKEN_CASES}
    assert set(FIXTURE["request_schema"]) == {case_id for case_id, _ in oracle.SCHEMA_CASES}
    assert set(FIXTURE["server"]["file_sha256"]) == set(oracle.SERVER_FILES)
    # Each verdict was produced from exactly the bytes these rows put on the
    # wire, so a changed row cannot silently keep a stale verdict.
    for row_id, payload in REQUEST_ROWS:
        assert FIXTURE["requests"][row_id]["wire_sha256"] == oracle.sha256(oracle.wire_bytes(payload)), row_id
    for case_id, raw in oracle.TOKEN_CASES:
        assert FIXTURE["tokens"][case_id]["raw_sha256"] == oracle.sha256(raw), case_id


def test_parity_table_codes_are_the_executed_server_codes():
    # The hard-coded server column of test_bridge_policy_parity.REQUEST_ROWS
    # is what the server returned for that row.
    for row in parity.REQUEST_ROWS:
        payload, table_code = row.values
        recorded = FIXTURE["requests"][row.id]["body"]["error_code"]
        executed = parity.ACCEPT if recorded in oracle.ACCEPTED_CODES else recorded
        assert executed == table_code, row.id


@pytest.mark.parametrize(("row_id", "payload"), REQUEST_ROWS, ids=[row_id for row_id, _ in REQUEST_ROWS])
def test_client_reproduces_the_executed_server_response(row_id, payload):
    server = FIXTURE["requests"][row_id]
    result, post = _download(payload)

    if server["body"]["error_code"] in oracle.ACCEPTED_CODES:
        # The server would run browser work for these bytes: the client sends
        # exactly them.
        post.assert_called_once()
        sent = post.call_args.kwargs["json"]
        assert sent == {**payload, "allowed_domains": [domain.lower() for domain in payload["allowed_domains"]]}
        assert result.request_id == payload["request_id"]
    else:
        # Refused locally with the server's whole error envelope, and nothing
        # is sent.
        post.assert_not_called()
        assert {key: getattr(result, key) for key in _ENVELOPE_KEYS} == server["body"]


def test_payload_with_no_json_form_is_refused_as_invalid_json():
    # A value JSON cannot represent at all has no wire bytes, so the server
    # has no verdict for it; the client refuses it like any body that is not
    # JSON, before transport.
    payload = parity._payload(doi=b"10.1000/example")
    result, post = _download(payload)

    post.assert_not_called()
    assert (result.error_code, result.request_id, result.message) == ("INVALID_JSON", None, "Invalid JSON body.")


def _clear_env(monkeypatch):
    for name in _TOKEN_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(("case_id", "raw"), oracle.TOKEN_CASES, ids=[case_id for case_id, _ in oracle.TOKEN_CASES])
@pytest.mark.parametrize("source", ["argument", "BRIDGE_TOKEN", "BRIDGE_AUTH_TOKEN", "ZOTERO_BRIDGE_TOKEN"])
def test_token_normalization_matches_validate_bridge_token(monkeypatch, case_id, raw, source):
    _clear_env(monkeypatch)
    server_token = FIXTURE["tokens"][case_id]["server"]
    if source == "argument":
        client = BridgeClient(base_url="http://127.0.0.1:9870", auth_token=raw)
    else:
        monkeypatch.setenv(source, raw)
        client = BridgeClient()
    assert client.configured is True

    payload = parity._payload()
    response = _accepted_response(parity.REQUEST_ID)
    with (
        patch("zotero_mcp.acquisition.bridge_client.httpx.post", return_value=response) as post,
        patch("zotero_mcp.acquisition.bridge_client.httpx.get", return_value=Mock(status_code=503, headers={})) as get,
    ):
        result = client.download(parity._request_from(payload))
        health = client.health()

    if server_token is None:
        # The server refuses this configured token; the client never sends it.
        assert result.error_code == BRIDGE_AUTH_INVALID
        assert health.error_code == BRIDGE_AUTH_INVALID
        post.assert_not_called()
        get.assert_not_called()
    else:
        # The server compares the bearer header with its trimmed token.
        post.assert_called_once()
        get.assert_called_once()
        assert post.call_args.kwargs["headers"]["Authorization"] == f"Bearer {server_token}"
        assert get.call_args.kwargs["headers"]["Authorization"] == f"Bearer {server_token}"
        assert result.error_code == "CAPABILITY_UNAVAILABLE"


def test_client_request_defaults_are_the_server_schema_defaults():
    recorded = FIXTURE["request_schema"]
    defaults = recorded["without-expected-artifact-and-timeout"]
    request = BridgeDownloadRequest(
        doi="10.1000/example", candidate_url="https://publisher.example/paper.pdf", session_name="campus"
    )
    assert (request.expected_artifact, request.timeout_ms) == (defaults["expected_artifact"], defaults["timeout_ms"])
    # The server parses the client's explicit request and the same request
    # with both keys omitted identically.
    assert recorded["client-request"] == defaults

    request.request_id = parity.REQUEST_ID
    with patch(
        "zotero_mcp.acquisition.bridge_client.httpx.post", return_value=_accepted_response(parity.REQUEST_ID)
    ) as post:
        BridgeClient(auth_token=oracle.VALID_TOKEN).download(request)
    sent = post.call_args.kwargs["json"]
    # The client always sends both keys, so the server defaults never apply.
    assert sent["expected_artifact"] == defaults["expected_artifact"]
    assert sent["timeout_ms"] == defaults["timeout_ms"]
    assert sent == recorded["client-request"]


def _server_repo() -> Path:
    value = os.environ.get("ZOTERO_BRIDGE_SERVER_REPO")
    if not value:
        pytest.fail("bridge_server tests need ZOTERO_BRIDGE_SERVER_REPO (a deep-research checkout) and bun")
    return Path(value).resolve()


@pytest.mark.bridge_server
def test_recording_is_what_the_server_returns_now():
    repo = _server_repo()
    live = oracle.build_fixture(repo)
    assert live["server"]["file_sha256"] == FIXTURE["server"]["file_sha256"]
    assert live["requests"] == FIXTURE["requests"]
    assert live["tokens"] == FIXTURE["tokens"]
    assert live["request_schema"] == FIXTURE["request_schema"]

"""Transport failures that are not httpx errors stay terminal bridge results.

A custom transport, a TLS or socket layer, or a proxy shim can raise any
exception from inside ``httpx.get``/``httpx.post``. ``BridgeClient`` must turn
each into its own bridge result (never propagate it, never leak the token)
and ``acquire_paper`` must stop on it without the direct downloader. Task
cancellation and interpreter exits still propagate.
"""

import asyncio
import ssl
from unittest.mock import patch

import pytest
from test_bridge_no_fallback import CLIENT, READY, TOKEN, _acquire, _assert_terminal, configured_bridge, downloader

from zotero_mcp.acquisition.bridge_client import BridgeClient, BridgeDownloadRequest

__all__ = ["configured_bridge", "downloader"]  # pytest fixtures used below

REQUEST_ID = "11111111-1111-4111-8111-111111111111"


def _request() -> BridgeDownloadRequest:
    return BridgeDownloadRequest(
        "10.1000/example",
        "https://publisher.example/paper.pdf",
        "campus",
        request_id=REQUEST_ID,
        allowed_domains=["publisher.example"],
    )


# (exception, download error_code). Each message carries the bearer token, as
# a careless transport might; it must not reach the result.
NON_HTTPX_ERRORS = [
    pytest.param(RuntimeError(f"transport exploded Bearer {TOKEN}"), "BRIDGE_UNREACHABLE", id="runtime-error"),
    pytest.param(OSError(f"socket failed Bearer {TOKEN}"), "BRIDGE_UNREACHABLE", id="os-error"),
    pytest.param(ConnectionResetError(f"reset Bearer {TOKEN}"), "BRIDGE_UNREACHABLE", id="connection-reset"),
    pytest.param(ssl.SSLError(f"tls Bearer {TOKEN}"), "BRIDGE_UNREACHABLE", id="ssl-error"),
    pytest.param(ValueError(f"bad frame Bearer {TOKEN}"), "BRIDGE_UNREACHABLE", id="value-error"),
    pytest.param(KeyError(f"Bearer {TOKEN}"), "BRIDGE_UNREACHABLE", id="key-error"),
    pytest.param(TimeoutError(f"socket timeout Bearer {TOKEN}"), "TIMEOUT", id="builtin-timeout"),
]


@pytest.mark.parametrize(("error", "error_code"), NON_HTTPX_ERRORS)
def test_download_normalizes_non_httpx_transport_errors(error, error_code):
    with patch(f"{CLIENT}.httpx.post", side_effect=error) as post:
        result = BridgeClient(auth_token=TOKEN).download(_request())

    post.assert_called_once()
    assert result.status == "failed"
    assert result.error_code == error_code
    assert result.file_path is None
    assert TOKEN not in repr(result)


@pytest.mark.parametrize(("error", "_download_code"), NON_HTTPX_ERRORS)
def test_health_normalizes_non_httpx_transport_errors(error, _download_code):
    with patch(f"{CLIENT}.httpx.get", side_effect=error) as get:
        health = BridgeClient(auth_token=TOKEN).health()

    get.assert_called_once()
    assert health.available is False
    assert health.status == "unreachable"
    assert health.error_code == "BRIDGE_UNREACHABLE"
    assert TOKEN not in repr(health)


@pytest.mark.parametrize("error", [KeyboardInterrupt(), SystemExit(1), asyncio.CancelledError()])
def test_non_exception_signals_still_propagate(error):
    with patch(f"{CLIENT}.httpx.post", side_effect=error), pytest.raises(type(error)):
        BridgeClient(auth_token=TOKEN).download(_request())
    with patch(f"{CLIENT}.httpx.get", side_effect=error), pytest.raises(type(error)):
        BridgeClient(auth_token=TOKEN).health()


@pytest.mark.asyncio
@pytest.mark.parametrize(("error", "_download_code"), NON_HTTPX_ERRORS)
async def test_acquire_stops_on_non_httpx_health_failure(configured_bridge, downloader, error, _download_code):
    result, http_get, http_post = await _acquire(get=error)

    _assert_terminal(result, "BRIDGE_UNREACHABLE", downloader)
    http_get.assert_called_once()
    http_post.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(("error", "error_code"), NON_HTTPX_ERRORS)
async def test_acquire_stops_on_non_httpx_download_failure(configured_bridge, downloader, error, error_code):
    result, http_get, http_post = await _acquire(get=READY, post=error)

    _assert_terminal(result, error_code, downloader)
    http_get.assert_called_once()
    http_post.assert_called_once()

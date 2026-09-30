"""A configured bridge is the only acquisition channel.

These tests drive ``acquire_paper`` through the real bridge client, with the
bridge configured from the environment and HTTP mocked at the httpx
boundary. Every bridge failure must be terminal with its own error code and
must never start the direct HTTP downloader.
"""

from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from zotero_mcp.acquisition.config import AcquisitionConfig, InstitutionalConfig
from zotero_mcp.acquisition.types import AccessLocation, AccessResolution, ArtifactDownload
from zotero_mcp.tools.acquire_paper import acquire_paper

TOKEN = "NoFallbackToken_0123456789abcdefghijklmnopqrstuvwxyz"
CLIENT = "zotero_mcp.acquisition.bridge_client"
TOOL = "zotero_mcp.tools.acquire_paper"


def _json(status_code: int, payload: object) -> Mock:
    response = Mock()
    response.status_code = status_code
    response.headers = {"content-type": "application/json; charset=utf-8"}
    response.json.return_value = payload
    return response


def _health(status: str = "ready") -> dict:
    ready = status == "ready"
    return {
        "contract_version": "2.0.0",
        "service": "pkb-browser-bridge",
        "status": status,
        "error_code": None if ready else "CAPABILITY_UNAVAILABLE",
        "capabilities": {"browser_get_version": ready, "fetch_interception": ready},
        "sessions": {"configured": 1, "ready": 1 if ready else 0},
    }


READY = _json(200, _health())
NOT_READY = _json(503, _health("not_ready"))
UNAUTHORIZED = _json(
    401,
    {
        "contract_version": "2.0.0",
        "request_id": None,
        "status": "failed",
        "auth_state": "unchecked",
        "error_code": "UNAUTHORIZED",
        "message": "Authentication required.",
    },
)


def _server_error(code: str, http_status: int, message: str, auth_state: str = "unchecked") -> dict:
    return {
        "contract_version": "2.0.0",
        "status": "failed",
        "auth_state": auth_state,
        "error_code": code,
        "message": message,
        "_http_status": http_status,
    }


def _echoing_post(error: dict):
    def post(url, json, headers, timeout):
        body = {key: value for key, value in error.items() if key != "_http_status"}
        return _json(error["_http_status"], {**body, "request_id": json["request_id"]})

    return post


def _resolution(url: str = "https://publisher.example/paper.pdf", **location_fields) -> AccessResolution:
    location = AccessLocation(url=url, access_method="institutional", **location_fields)
    return AccessResolution(
        identifier_type="doi",
        identifier_value="10.1000/example",
        locations=[location],
        best_location=location,
        metadata={"title": "Example"},
    )


@pytest.fixture
def configured_bridge(monkeypatch):
    monkeypatch.setenv("BRIDGE_SERVER_URL", "http://127.0.0.1:9870")
    monkeypatch.setenv("ZOTERO_BRIDGE_TOKEN", TOKEN)


@pytest.fixture
def downloader():
    with patch(f"{TOOL}.ArtifactDownloader") as downloader_class:
        downloader_class.return_value.download = AsyncMock()
        yield downloader_class


def _effect(behavior):
    # A response is returned, an exception is raised, a function is called.
    if isinstance(behavior, Mock):
        return lambda *args, **kwargs: behavior
    return behavior


async def _acquire(resolution=None, config=None, session_name="campus", get=READY, post=None):
    with (
        patch(f"{TOOL}.load_acquisition_config", return_value=config or AcquisitionConfig()),
        patch(f"{TOOL}.resolve_access", new=AsyncMock(return_value=resolution or _resolution())),
        patch(f"{CLIENT}.httpx.get", side_effect=_effect(get)) as http_get,
        patch(f"{CLIENT}.httpx.post", side_effect=_effect(post)) as http_post,
    ):
        result = await acquire_paper("10.1000/example", session_name=session_name)
    return result, http_get, http_post


def _assert_terminal(result: dict, error_code: str, downloader) -> None:
    assert result["status"] == "failed"
    assert result["error_code"] == error_code
    assert result["message"].startswith(f"[{error_code}]")
    assert TOKEN not in repr(result)
    downloader.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("get", "error_code"),
    [
        pytest.param(httpx.ConnectError("connection refused"), "BRIDGE_UNREACHABLE", id="unreachable"),
        pytest.param(httpx.ReadTimeout("timed out"), "BRIDGE_UNREACHABLE", id="health-timeout"),
        pytest.param(NOT_READY, "CAPABILITY_UNAVAILABLE", id="not-ready"),
        pytest.param(_json(200, {"status": "ok"}), "BRIDGE_HEALTH_INVALID", id="invalid-readiness-body"),
        pytest.param(
            _json(200, {"contract_version": "2.0.0", "service": "pkb-browser-bridge", "status": "alive"}),
            "BRIDGE_HEALTH_INVALID",
            id="liveness-body-on-readiness",
        ),
        pytest.param(UNAUTHORIZED, "UNAUTHORIZED", id="token-rejected"),
    ],
)
async def test_configured_bridge_health_failure_is_terminal(configured_bridge, downloader, get, error_code):
    result, http_get, http_post = await _acquire(get=get)

    _assert_terminal(result, error_code, downloader)
    http_get.assert_called_once()
    http_post.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "env",
    [
        pytest.param({"BRIDGE_SERVER_URL": "http://127.0.0.1:9870"}, id="url-without-token"),
        pytest.param({"ZOTERO_BRIDGE_TOKEN": "short"}, id="malformed-token"),
        pytest.param({"BRIDGE_TOKEN": ""}, id="empty-token"),
    ],
)
async def test_configured_bridge_without_usable_auth_is_terminal(monkeypatch, downloader, env):
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    result, http_get, http_post = await _acquire()

    _assert_terminal(result, "BRIDGE_AUTH_INVALID", downloader)
    http_get.assert_not_called()
    http_post.assert_not_called()


@pytest.mark.asyncio
async def test_configured_bridge_without_session_is_terminal(configured_bridge, downloader):
    result, http_get, http_post = await _acquire(session_name=None)

    _assert_terminal(result, "BRIDGE_SESSION_REQUIRED", downloader)
    http_get.assert_not_called()
    http_post.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("post", "error_code"),
    [
        pytest.param(httpx.ConnectError("connection refused"), "BRIDGE_UNREACHABLE", id="download-unreachable"),
        pytest.param(httpx.ReadTimeout("timed out"), "TIMEOUT", id="download-timeout"),
        pytest.param(
            lambda *args, **kwargs: _json(200, {"status": "complete"}), "BRIDGE_RESPONSE_INVALID", id="invalid-body"
        ),
        pytest.param(_echoing_post(_server_error("NO_PDF", 404, "No PDF was found.")), "NO_PDF", id="no-pdf"),
        pytest.param(
            _echoing_post(_server_error("SESSION_BUSY", 409, "Browser session is busy.", "busy")),
            "SESSION_BUSY",
            id="session-busy",
        ),
        pytest.param(
            _echoing_post(_server_error("INTERNAL_ERROR", 500, "Internal bridge failure.")),
            "INTERNAL_ERROR",
            id="internal-error",
        ),
    ],
)
async def test_configured_bridge_download_failure_is_terminal(configured_bridge, downloader, post, error_code):
    result, http_get, sent = await _acquire(post=post)

    _assert_terminal(result, error_code, downloader)
    http_get.assert_called_once()
    sent.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("candidate_url", "error_code"),
    [
        pytest.param("http://publisher.example/paper.pdf", "URL_SCHEME_BLOCKED", id="scheme"),
        pytest.param("https://user@publisher.example/paper.pdf", "INVALID_REQUEST", id="userinfo"),
        pytest.param("https://10.0.0.1/paper.pdf", "DOMAIN_BLOCKED", id="ip-host"),
    ],
)
async def test_configured_bridge_policy_refusal_is_terminal(configured_bridge, downloader, candidate_url, error_code):
    result, http_get, http_post = await _acquire(resolution=_resolution(candidate_url))

    _assert_terminal(result, error_code, downloader)
    http_post.assert_not_called()


@pytest.mark.asyncio
async def test_direct_attempt_failure_does_not_fall_back(configured_bridge, downloader):
    # Direct-before-libproxy: a non-paywall failure of the direct attempt is
    # terminal; neither the libproxy URL nor the direct downloader is tried.
    config = AcquisitionConfig(
        institutional_access=InstitutionalConfig(
            enabled=True, provider="libproxy", libproxy_base_url="https://libproxy.example/login"
        )
    )
    resolution = _resolution(
        "https://libproxy.example/login?url=https://publisher.example/paper.pdf",
        requires_session=True,
        session_kind="libproxy",
    )

    result, _, http_post = await _acquire(
        resolution=resolution,
        config=config,
        post=_echoing_post(_server_error("NO_PDF", 404, "No PDF was found.")),
    )

    _assert_terminal(result, "NO_PDF", downloader)
    http_post.assert_called_once()
    assert http_post.call_args.kwargs["json"]["candidate_url"] == "https://doi.org/10.1000/example"


@pytest.mark.asyncio
async def test_unconfigured_bridge_uses_direct_downloader_only(downloader, tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    downloader.return_value.download = AsyncMock(
        return_value=ArtifactDownload(file_path=pdf, content_type="application/pdf", size_bytes=9)
    )

    result, http_get, http_post = await _acquire()

    assert result["status"] == "complete"
    assert result["message"] == "Downloaded via HTTP"
    downloader.return_value.download.assert_awaited_once()
    http_get.assert_not_called()
    http_post.assert_not_called()

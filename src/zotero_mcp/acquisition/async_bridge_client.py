from __future__ import annotations

import os

import httpx

from .bridge_client import (
    BRIDGE_SERVER_URL,
    BridgeClient,
    BridgeDownloadRequest,
    BridgeDownloadResult,
    _allowed_domains,
)


class AsyncBridgeClient:
    def __init__(self, base_url: str = BRIDGE_SERVER_URL, auth_token: str | None = None):
        self._sync_client = BridgeClient(base_url=base_url, auth_token=auth_token)

    async def download(self, request: BridgeDownloadRequest) -> BridgeDownloadResult:
        headers = self._sync_client._headers()
        if headers is None:
            return self._sync_client._unavailable_result()

        allowed_domains = _allowed_domains(request.candidate_url, os.environ.get("BRIDGE_ALLOWED_DOMAINS", ""))
        if not allowed_domains:
            return BridgeDownloadResult(
                status="failed",
                auth_state="missing",
                error_code="DOMAIN_BLOCKED",
                message="Candidate URL has no allowed domain",
            )

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self._sync_client.base_url}/bridge/download",
                    json={
                        "doi": request.doi,
                        "candidate_url": request.candidate_url,
                        "session_name": request.session_name,
                        "expected_artifact": request.expected_artifact,
                        "timeout_ms": request.timeout_ms,
                        "allowed_domains": allowed_domains,
                    },
                    headers=headers,
                    timeout=request.timeout_ms / 1000 + 5,
                )
        except httpx.TimeoutException:
            return self._sync_client._timeout_result()
        except httpx.HTTPError:
            return self._sync_client._unavailable_result()

        if response.status_code != 200:
            return self._sync_client._unavailable_result()
        return self._sync_client._parse_download_response(response) or self._sync_client._unavailable_result()

    async def is_available(self) -> bool:
        headers = self._sync_client._headers()
        if headers is None:
            return False
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self._sync_client.base_url}/bridge/health", headers=headers, timeout=2.0)
        except httpx.HTTPError:
            return False
        return self._sync_client._is_healthy_response(response)

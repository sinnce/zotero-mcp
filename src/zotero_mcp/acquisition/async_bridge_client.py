from __future__ import annotations

import asyncio

from .bridge_client import BridgeClient, BridgeDownloadRequest, BridgeDownloadResult, BridgeHealthResult


class AsyncBridgeClient:
    """Async facade over the contract-validating bridge client.

    The HTTP boundary remains bounded by the synchronous client's httpx
    timeout. Running it in a worker keeps the MCP acquisition coroutine from
    blocking while preserving one request/response parser for both call paths.
    """

    def __init__(self, base_url: str | None = None, auth_token: str | None = None):
        self._sync_client = BridgeClient(base_url=base_url, auth_token=auth_token)

    async def download(self, request: BridgeDownloadRequest) -> BridgeDownloadResult:
        return await asyncio.to_thread(self._sync_client.download, request)

    async def health(self) -> BridgeHealthResult:
        return await asyncio.to_thread(self._sync_client.health)

    async def is_available(self) -> bool:
        return await asyncio.to_thread(self._sync_client.is_available)

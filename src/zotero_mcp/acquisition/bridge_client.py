from __future__ import annotations
import httpx
from dataclasses import dataclass
from typing import Optional

BRIDGE_SERVER_URL = "http://127.0.0.1:9870"


@dataclass
class BridgeDownloadRequest:
    doi: str
    candidate_url: str
    session_name: str
    expected_artifact: str = "pdf"
    timeout_ms: int = 60000


@dataclass
class BridgeDownloadResult:
    status: str
    auth_state: str
    file_path: Optional[str] = None
    error_code: Optional[str] = None
    message: Optional[str] = None


class BridgeClient:
    def __init__(self, base_url: str = BRIDGE_SERVER_URL):
        self.base_url = base_url

    def download(self, request: BridgeDownloadRequest) -> BridgeDownloadResult:
        try:
            resp = httpx.post(
                f"{self.base_url}/bridge/download",
                json={
                    "doi": request.doi,
                    "candidate_url": request.candidate_url,
                    "session_name": request.session_name,
                    "expected_artifact": request.expected_artifact,
                    "timeout_ms": request.timeout_ms,
                },
                timeout=request.timeout_ms / 1000 + 5,
            )
            resp.raise_for_status()
            data = resp.json()
            return BridgeDownloadResult(**{k: data.get(k) for k in BridgeDownloadResult.__dataclass_fields__})
        except Exception as e:
            return BridgeDownloadResult(
                status="failed",
                auth_state="missing",
                error_code="TIMEOUT",
                message=str(e),
            )

    def is_available(self) -> bool:
        try:
            resp = httpx.get(f"{self.base_url}/bridge/health", timeout=2.0)
            return resp.status_code == 200
        except Exception:
            return False

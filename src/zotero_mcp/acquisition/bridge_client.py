from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Final, Literal
from urllib.parse import parse_qsl, urlparse

import httpx

BRIDGE_SERVER_URL = "http://127.0.0.1:9870"
_MAX_ALLOWED_DOMAINS = 32
_MAX_DOMAIN_LENGTH = 253
_MAX_NESTED_URL_DEPTH = 2
BridgeStatus = Literal["complete", "failed", "auth_required"]
BridgeAuthState = Literal["ready", "missing", "expired"]
BridgeErrorCode = Literal[
    "ACCESS_DENIED",
    "AUTH_REQUIRED",
    "BRIDGE_UNAVAILABLE",
    "DOMAIN_BLOCKED",
    "HTML_LANDING",
    "TIMEOUT",
]
_VALID_STATUSES: Final[frozenset[str]] = frozenset({"complete", "failed", "auth_required"})
_VALID_AUTH_STATES: Final[frozenset[str]] = frozenset({"ready", "missing", "expired"})
_VALID_ERROR_CODES: Final[frozenset[str]] = frozenset(
    {"ACCESS_DENIED", "AUTH_REQUIRED", "BRIDGE_UNAVAILABLE", "DOMAIN_BLOCKED", "HTML_LANDING", "TIMEOUT"}
)
_DOMAIN_PATTERN = re.compile(
    r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z"
)


@dataclass
class BridgeDownloadRequest:
    doi: str
    candidate_url: str
    session_name: str
    expected_artifact: str = "pdf"
    timeout_ms: int = 60000


@dataclass
class BridgeDownloadResult:
    status: BridgeStatus
    auth_state: BridgeAuthState
    file_path: str | None = None
    error_code: BridgeErrorCode | None = None
    message: str | None = None


def _normalize_domain(value: str) -> str | None:
    domain = value.strip().lower().rstrip(".")
    if len(domain) > _MAX_DOMAIN_LENGTH or not _DOMAIN_PATTERN.fullmatch(domain):
        return None
    return domain


def _candidate_domains(candidate_url: str) -> list[str]:
    domains: list[str] = []
    pending = [(candidate_url, 0)]
    while pending:
        url, depth = pending.pop(0)
        try:
            parsed = urlparse(url)
        except ValueError:
            continue
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            continue
        domain = _normalize_domain(parsed.hostname)
        if domain and domain not in domains:
            domains.append(domain)
        if depth < _MAX_NESTED_URL_DEPTH:
            pending.extend(
                (value, depth + 1)
                for key, value in parse_qsl(parsed.query, keep_blank_values=False)
                if key.lower() == "url"
            )
    return domains


def _allowed_domains(candidate_url: str, extra_domains: str) -> list[str]:
    domains = _candidate_domains(candidate_url)
    for value in extra_domains.split(","):
        domain = _normalize_domain(value)
        if domain and domain not in domains:
            domains.append(domain)
    return domains[:_MAX_ALLOWED_DOMAINS]


class BridgeClient:
    def __init__(self, base_url: str = BRIDGE_SERVER_URL, auth_token: str | None = None):
        self.base_url = base_url.rstrip("/")
        self._auth_token = auth_token if auth_token is not None else os.environ.get("BRIDGE_AUTH_TOKEN", "")

    def _headers(self) -> dict[str, str] | None:
        if not self._auth_token:
            return None
        return {"Authorization": f"Bearer {self._auth_token}"}

    def _unavailable_result(self) -> BridgeDownloadResult:
        return BridgeDownloadResult(
            status="failed",
            auth_state="missing",
            error_code="BRIDGE_UNAVAILABLE",
            message="Bridge authentication is unavailable",
        )

    def _timeout_result(self) -> BridgeDownloadResult:
        return BridgeDownloadResult(
            status="failed",
            auth_state="missing",
            error_code="TIMEOUT",
            message="Bridge request failed",
        )

    def _parse_download_response(self, response: httpx.Response) -> BridgeDownloadResult | None:
        try:
            payload = response.json()
        except ValueError:
            return None
        if not isinstance(payload, dict):
            return None

        status = payload.get("status")
        auth_state = payload.get("auth_state")
        error_code = payload.get("error_code")
        if status not in _VALID_STATUSES or auth_state not in _VALID_AUTH_STATES:
            return None
        if status == "complete":
            if error_code is not None:
                return None
            file_path = payload.get("file_path")
            return BridgeDownloadResult(
                status=status,
                auth_state=auth_state,
                file_path=file_path if isinstance(file_path, str) else None,
            )
        if error_code not in _VALID_ERROR_CODES:
            return None
        return BridgeDownloadResult(status=status, auth_state=auth_state, error_code=error_code)

    def download(self, request: BridgeDownloadRequest) -> BridgeDownloadResult:
        headers = self._headers()
        if headers is None:
            return self._unavailable_result()

        allowed_domains = _allowed_domains(request.candidate_url, os.environ.get("BRIDGE_ALLOWED_DOMAINS", ""))
        if not allowed_domains:
            return BridgeDownloadResult(
                status="failed",
                auth_state="missing",
                error_code="DOMAIN_BLOCKED",
                message="Candidate URL has no allowed domain",
            )

        try:
            resp = httpx.post(
                f"{self.base_url}/bridge/download",
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
        except httpx.HTTPError:
            return self._timeout_result()

        result = self._parse_download_response(resp)
        if result is not None:
            return result
        if resp.status_code == 401:
            return BridgeDownloadResult(
                status="failed",
                auth_state="missing",
                error_code="AUTH_REQUIRED",
                message="Bridge authentication was rejected",
            )
        return self._unavailable_result()

    def is_available(self) -> bool:
        headers = self._headers()
        if headers is None:
            return False
        try:
            resp = httpx.get(f"{self.base_url}/bridge/health", headers=headers, timeout=2.0)
        except httpx.HTTPError:
            return False
        return resp.status_code == 200

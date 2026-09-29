from __future__ import annotations

import contextlib
import hashlib
import hmac
import ipaddress
import os
import re
import stat
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final, Literal
from urllib.parse import parse_qsl, urlparse

import httpx

BRIDGE_SERVER_URL = "http://127.0.0.1:9870"
BRIDGE_CONTRACT_VERSION = "2.0.0"
BRIDGE_SERVICE = "pkb-browser-bridge"
_MAX_ALLOWED_DOMAINS = 32
_MAX_DOMAIN_LENGTH = 253
_MAX_NESTED_URL_DEPTH = 2
_MAX_FILE_PATH_LENGTH = 4096
_MAX_ARTIFACT_BYTES = 100 * 1024 * 1024
_ARTIFACT_READ_CHUNK = 1024 * 1024

# Client-side integrity failures for a staged bridge artifact. They are not
# bridge wire codes: the client emits them after re-reading the staged file.
ARTIFACT_UNVERIFIABLE = "ARTIFACT_UNVERIFIABLE"
ARTIFACT_MISSING = "ARTIFACT_MISSING"
ARTIFACT_NOT_REGULAR = "ARTIFACT_NOT_REGULAR"
ARTIFACT_UNREADABLE = "ARTIFACT_UNREADABLE"
ARTIFACT_SIZE_MISMATCH = "ARTIFACT_SIZE_MISMATCH"
ARTIFACT_HASH_MISMATCH = "ARTIFACT_HASH_MISMATCH"

BridgeStatus = Literal["complete", "failed", "auth_required"]
BridgeAuthState = Literal[
    "ready",
    "expired",
    "missing",
    "interactive_required",
    "unchecked",
    "ambiguous",
    "busy",
]
BridgeErrorCode = str

_VALID_STATUSES: Final[frozenset[str]] = frozenset({"complete", "failed", "auth_required"})
_VALID_AUTH_STATES: Final[frozenset[str]] = frozenset(
    {"ready", "expired", "missing", "interactive_required", "unchecked", "ambiguous", "busy"}
)
# Status, authentication state, HTTP status, and public message are frozen by
# the deep-research browser-bridge 2.0.0 response contract.
_ERROR_DETAILS: Final[dict[str, tuple[int, str]]] = {
    "INVALID_JSON": (400, "Invalid JSON body."),
    "INVALID_REQUEST": (400, "Invalid request."),
    "UNSUPPORTED_CONTRACT_VERSION": (400, "Unsupported contract version."),
    "LEGACY_DISABLED": (400, "Legacy bridge protocol is disabled."),
    "UNAUTHORIZED": (401, "Authentication required."),
    "NOT_FOUND": (404, "Route not found."),
    "TRANSFER_NOT_FOUND": (404, "Transfer not found."),
    "NO_PDF": (404, "No PDF was found."),
    "ACCESS_DENIED": (403, "Destination denied access."),
    "METHOD_NOT_ALLOWED": (405, "Method not allowed."),
    "ACK_CONFLICT": (409, "Transfer acknowledgement conflicts."),
    "SESSION_BUSY": (409, "Browser session is busy."),
    "TRANSFER_EXPIRED": (410, "Transfer expired."),
    "REQUEST_TOO_LARGE": (413, "Request body too large."),
    "UNSUPPORTED_MEDIA_TYPE": (415, "Unsupported request encoding."),
    "SESSION_UNKNOWN": (422, "Unknown browser session."),
    "URL_SCHEME_BLOCKED": (422, "URL scheme is blocked."),
    "LEGACY_POLICY_UNRESOLVED": (422, "Legacy destination policy could not be resolved."),
    "DOMAIN_BLOCKED": (422, "Destination domain is blocked."),
    "DESTINATION_UNRESOLVED": (422, "Destination could not be resolved."),
    "ADDRESS_BLOCKED": (422, "Destination address is blocked."),
    "AUTH_EXPIRED": (428, "Browser authentication expired."),
    "AUTH_MISSING": (428, "Browser authentication is missing."),
    "INTERACTIVE_REQUIRED": (428, "Interactive browser authentication required."),
    "CAPTCHA": (428, "Browser challenge requires interaction."),
    "AUTH_REQUIRED": (428, "Browser authentication required."),
    "AUTH_AMBIGUOUS": (503, "Browser authentication state is ambiguous."),
    "CAPABILITY_UNAVAILABLE": (503, "Browser interception is unavailable."),
    "TIMEOUT": (504, "Request deadline exceeded."),
    "INTERNAL_ERROR": (500, "Internal bridge failure."),
    "STORAGE_FAILURE": (500, "Artifact staging failed."),
    "HTML_LANDING": (502, "Destination returned an HTML landing page."),
    "INVALID_ARTIFACT": (502, "Downloaded artifact is invalid."),
}
_ERROR_STATUS: Final[dict[str, tuple[str, str]]] = {
    "AUTH_EXPIRED": ("auth_required", "expired"),
    "AUTH_MISSING": ("auth_required", "missing"),
    "INTERACTIVE_REQUIRED": ("auth_required", "interactive_required"),
    "CAPTCHA": ("auth_required", "interactive_required"),
    "AUTH_REQUIRED": ("auth_required", "expired"),
    "SESSION_UNKNOWN": ("failed", "missing"),
    "SESSION_BUSY": ("failed", "busy"),
    "AUTH_AMBIGUOUS": ("failed", "ambiguous"),
}
_DOMAIN_PATTERN = re.compile(
    r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z"
)


@dataclass
class BridgeDownloadRequest:
    doi: str
    candidate_url: str
    session_name: str
    expected_artifact: str = "pdf"
    timeout_ms: int = 60000
    # Appended for positional compatibility with the v1 request object.
    request_id: str | None = None
    allowed_domains: tuple[str, ...] | list[str] | None = None
    contract_version: str = BRIDGE_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if self.request_id is None:
            self.request_id = str(uuid.uuid4())


@dataclass
class BridgeDownloadResult:
    status: BridgeStatus
    auth_state: BridgeAuthState
    file_path: str | None = None
    error_code: BridgeErrorCode | None = None
    message: str | None = None
    contract_version: str | None = None
    request_id: str | None = None
    transfer_id: str | None = None
    sha256: str | None = None
    size_bytes: int | None = None
    content_type: str | None = None
    final_url: str | None = None
    expires_at: str | None = None
    ack_required: bool | None = None


@dataclass
class BridgeHealthResult:
    available: bool
    base_url: str
    status: str
    message: str
    port: int | None = None
    contract_version: str | None = None
    service: str | None = None
    error_code: str | None = None
    capabilities: dict[str, bool] | None = None
    configured_sessions: int | None = None
    ready_sessions: int | None = None


def _normalize_domain(value: str) -> str | None:
    value = value.strip().lower()
    if value.endswith(".") or len(value) > _MAX_DOMAIN_LENGTH:
        return None
    if not _DOMAIN_PATTERN.fullmatch(value):
        return None
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return value
    return None


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


def _allowed_domains(
    candidate_url: str,
    extra_domains: str,
    requested_domains: tuple[str, ...] | list[str] | None = None,
) -> list[str]:
    # An explicit request allowlist is authoritative. The derived candidate
    # hosts are only a compatibility default for callers that do not provide
    # one; the bridge server remains the final policy authority.
    domains = [] if requested_domains is not None else _candidate_domains(candidate_url)
    values = requested_domains if requested_domains is not None else extra_domains.split(",")
    for value in values:
        domain = _normalize_domain(value)
        if domain and domain not in domains:
            domains.append(domain)
    return domains[:_MAX_ALLOWED_DOMAINS]


def _is_uuid(value: object) -> bool:
    if not isinstance(value, str) or not re.fullmatch(
        r"(?:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|"
        r"00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)",
        value,
    ):
        return False
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def _is_valid_final_url(value: object, allowed_domains: list[str]) -> bool:
    if (
        not isinstance(value, str)
        or len(value) > 2048
        or not re.fullmatch(r"[\x21-\x7e]+", value)
        or re.search(r"%(?![0-9a-fA-F]{2})", value)
    ):
        return False
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    host = _normalize_domain(parsed.hostname or "")
    if not host or parsed.scheme.lower() != "https" or parsed.username or parsed.password or parsed.fragment:
        return False
    return host in allowed_domains or any(host.endswith(f".{domain}") for domain in allowed_domains)


def _is_valid_candidate_url(value: object) -> bool:
    if (
        not isinstance(value, str)
        or len(value) > 2048
        or not re.fullmatch(r"[\x21-\x7e]+", value)
        or re.search(r"%(?![0-9a-fA-F]{2})", value)
    ):
        return False
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    host = _normalize_domain(parsed.hostname or "")
    return bool(
        host
        and parsed.scheme.lower() == "https"
        and not parsed.username
        and not parsed.password
        and not parsed.fragment
    )


def _is_datetime(value: object) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", value):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def verify_bridge_artifact(result: BridgeDownloadResult) -> str | None:
    """Re-hash a staged bridge artifact against its declared identity.

    Returns ``None`` only when ``file_path`` names a regular, non-symlink file
    whose byte length equals ``size_bytes`` and whose SHA-256 equals
    ``sha256``; otherwise returns a distinct ``ARTIFACT_*`` error code.
    """
    error_code, _ = _check_bridge_artifact(result, None)
    return error_code


def snapshot_bridge_artifact(
    result: BridgeDownloadResult, dest_dir: str | os.PathLike[str]
) -> tuple[str | None, Path | None]:
    """Verify a staged bridge artifact while copying it into ``dest_dir``.

    The staged path stays owned by the bridge and can be replaced after any
    separate check, so consumers must read the returned snapshot: its bytes
    are exactly the bytes that were hashed. ``dest_dir`` must be a private
    directory owned by the caller. Returns ``(None, snapshot_path)`` on
    success, or ``(error_code, None)`` with no snapshot left behind.
    """
    return _check_bridge_artifact(result, Path(dest_dir))


def _check_bridge_artifact(result: BridgeDownloadResult, dest_dir: Path | None) -> tuple[str | None, Path | None]:
    file_path = result.file_path
    declared_sha256 = result.sha256
    declared_size = result.size_bytes
    if (
        not isinstance(file_path, str)
        or not file_path.startswith("/")
        or "\0" in file_path
        or len(file_path) > _MAX_FILE_PATH_LENGTH
        or not isinstance(declared_sha256, str)
        or not re.fullmatch(r"[0-9a-f]{64}", declared_sha256)
        or isinstance(declared_size, bool)
        or not isinstance(declared_size, int)
        or not 1 <= declared_size <= _MAX_ARTIFACT_BYTES
    ):
        return ARTIFACT_UNVERIFIABLE, None
    try:
        link_stat = os.lstat(file_path)
    except FileNotFoundError:
        return ARTIFACT_MISSING, None
    except OSError:
        return ARTIFACT_UNREADABLE, None
    if not stat.S_ISREG(link_stat.st_mode):
        return ARTIFACT_NOT_REGULAR, None
    # O_NOFOLLOW rejects a symlink swapped in after lstat; O_NONBLOCK keeps a
    # swapped-in FIFO from blocking the open.
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(file_path, flags)
    except FileNotFoundError:
        return ARTIFACT_MISSING, None
    except OSError:
        return ARTIFACT_NOT_REGULAR, None
    snapshot_path: Path | None = None
    error_code: str | None = None
    digest = hashlib.sha256()
    total = 0
    try:
        with os.fdopen(fd, "rb") as handle:
            opened_stat = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened_stat.st_mode) or (opened_stat.st_dev, opened_stat.st_ino) != (
                link_stat.st_dev,
                link_stat.st_ino,
            ):
                return ARTIFACT_NOT_REGULAR, None
            if opened_stat.st_size != declared_size:
                return ARTIFACT_SIZE_MISMATCH, None
            sink = None
            if dest_dir is not None:
                target = dest_dir / _snapshot_name(file_path)
                sink_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                sink = os.fdopen(os.open(target, sink_flags | getattr(os, "O_CLOEXEC", 0), 0o600), "wb")
                snapshot_path = target
            try:
                while chunk := handle.read(_ARTIFACT_READ_CHUNK):
                    total += len(chunk)
                    if total > declared_size:
                        error_code = ARTIFACT_SIZE_MISMATCH
                        break
                    digest.update(chunk)
                    if sink is not None:
                        sink.write(chunk)
            finally:
                if sink is not None:
                    sink.close()
    except OSError:
        error_code = ARTIFACT_UNREADABLE
    if error_code is None and total != declared_size:
        error_code = ARTIFACT_SIZE_MISMATCH
    if error_code is None and not hmac.compare_digest(digest.hexdigest(), declared_sha256):
        error_code = ARTIFACT_HASH_MISMATCH
    if error_code is not None:
        if snapshot_path is not None:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(snapshot_path)
        return error_code, None
    return None, snapshot_path


def _snapshot_name(file_path: str) -> str:
    # Keep the staged basename: ingest uses it as the attachment filename.
    name = os.path.basename(file_path)
    return name if name not in ("", ".", "..") else "artifact"


class BridgeClient:
    def __init__(self, base_url: str | None = None, auth_token: str | None = None):
        configured_url = base_url if base_url is not None else os.environ.get("BRIDGE_SERVER_URL", BRIDGE_SERVER_URL)
        self.base_url = configured_url.rstrip("/")
        self._auth_token = (
            auth_token
            if auth_token is not None
            else os.environ.get("ZOTERO_BRIDGE_TOKEN")
            or os.environ.get("BRIDGE_AUTH_TOKEN")
            or os.environ.get("BRIDGE_TOKEN", "")
        )

    def _headers(self) -> dict[str, str] | None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", self._auth_token):
            return None
        return {
            "Authorization": f"Bearer {self._auth_token}",
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
        }

    def _unavailable_result(self, message: str = "Bridge is unavailable") -> BridgeDownloadResult:
        return BridgeDownloadResult(
            status="failed", auth_state="missing", error_code="BRIDGE_UNAVAILABLE", message=message
        )

    def _timeout_result(self) -> BridgeDownloadResult:
        return BridgeDownloadResult(
            status="failed", auth_state="missing", error_code="TIMEOUT", message="Bridge request failed"
        )

    def _parse_download_response(
        self,
        response: httpx.Response,
        request_id: str,
        allowed_domains: list[str],
    ) -> BridgeDownloadResult | None:
        if not response.headers.get("content-type", "").lower().startswith("application/json"):
            return None
        try:
            payload = response.json()
        except (TypeError, ValueError):
            return None
        if not isinstance(payload, dict) or payload.get("contract_version") != BRIDGE_CONTRACT_VERSION:
            return None
        status = payload.get("status")
        auth_state = payload.get("auth_state")
        if (
            not isinstance(status, str)
            or not isinstance(auth_state, str)
            or status not in _VALID_STATUSES
            or auth_state not in _VALID_AUTH_STATES
        ):
            return None
        if status == "complete":
            required = {
                "contract_version",
                "request_id",
                "status",
                "auth_state",
                "transfer_id",
                "file_path",
                "sha256",
                "size_bytes",
                "content_type",
                "final_url",
                "expires_at",
                "ack_required",
                "error_code",
            }
            if set(payload) != required:
                return None
            if (
                response.status_code != 200
                or auth_state != "ready"
                or payload.get("request_id") != request_id
                or not _is_uuid(payload.get("transfer_id"))
                or not isinstance(payload.get("file_path"), str)
                or not payload["file_path"].startswith("/")
                or "\0" in payload["file_path"]
                or len(payload["file_path"]) > _MAX_FILE_PATH_LENGTH
                or not isinstance(payload.get("sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", payload["sha256"])
                or isinstance(payload.get("size_bytes"), bool)
                or not isinstance(payload.get("size_bytes"), int)
                or not 1 <= payload["size_bytes"] <= _MAX_ARTIFACT_BYTES
                or payload.get("content_type") != "application/pdf"
                or not _is_valid_final_url(payload.get("final_url"), allowed_domains)
                or not _is_datetime(payload.get("expires_at"))
                or payload.get("ack_required") is not True
                or payload.get("error_code") is not None
            ):
                return None
            return BridgeDownloadResult(
                status="complete",
                auth_state="ready",
                file_path=payload["file_path"],
                contract_version=payload["contract_version"],
                request_id=payload["request_id"],
                transfer_id=payload["transfer_id"],
                sha256=payload["sha256"],
                size_bytes=payload["size_bytes"],
                content_type=payload["content_type"],
                final_url=payload["final_url"],
                expires_at=payload["expires_at"],
                ack_required=True,
            )

        required = {"contract_version", "request_id", "status", "auth_state", "error_code", "message"}
        if set(payload) != required:
            return None
        error_code = payload.get("error_code")
        if not isinstance(error_code, str) or error_code not in _ERROR_DETAILS:
            return None
        response_id = payload.get("request_id")
        if response_id is not None and response_id != request_id:
            return None
        if error_code == "UNAUTHORIZED" and response_id is not None:
            return None
        expected_status, expected_state = _ERROR_STATUS.get(error_code, ("failed", "unchecked"))
        http_status, message = _ERROR_DETAILS[error_code]
        if (
            status != expected_status
            or auth_state != expected_state
            or response.status_code != http_status
            or payload.get("message") != message
        ):
            return None
        return BridgeDownloadResult(
            status=status,
            auth_state=auth_state,
            error_code=error_code,
            message=payload["message"],
            contract_version=payload["contract_version"],
            request_id=payload["request_id"],
        )

    def download(self, request: BridgeDownloadRequest) -> BridgeDownloadResult:
        headers = self._headers()
        if headers is None:
            return self._unavailable_result()
        if request.contract_version != BRIDGE_CONTRACT_VERSION:
            return self._unavailable_result("Unsupported bridge contract version")
        if not _is_valid_candidate_url(request.candidate_url):
            return BridgeDownloadResult(
                status="failed",
                auth_state="missing",
                error_code="DOMAIN_BLOCKED",
                message="Candidate URL is not allowed",
            )
        allowed_domains = _allowed_domains(
            request.candidate_url,
            os.environ.get("BRIDGE_ALLOWED_DOMAINS", ""),
            request.allowed_domains,
        )
        candidate_host = _normalize_domain(urlparse(request.candidate_url).hostname or "")
        if candidate_host is None or not any(
            candidate_host == domain or candidate_host.endswith(f".{domain}") for domain in allowed_domains
        ):
            return BridgeDownloadResult(
                status="failed",
                auth_state="missing",
                error_code="DOMAIN_BLOCKED",
                message="Candidate URL is not allowed",
            )
        if not allowed_domains:
            return BridgeDownloadResult(
                status="failed",
                auth_state="missing",
                error_code="DOMAIN_BLOCKED",
                message="Candidate URL has no allowed domain",
            )
        request_id = request.request_id
        if not isinstance(request_id, str) or not _is_uuid(request_id):
            return self._unavailable_result("Invalid bridge request id")
        payload = {
            "contract_version": request.contract_version,
            "request_id": request_id,
            "doi": request.doi,
            "candidate_url": request.candidate_url,
            "session_name": request.session_name,
            "expected_artifact": request.expected_artifact,
            "timeout_ms": request.timeout_ms,
            "allowed_domains": allowed_domains,
        }
        try:
            response = httpx.post(
                f"{self.base_url}/bridge/download",
                json=payload,
                headers=headers,
                timeout=request.timeout_ms / 1000 + 5,
            )
        except httpx.TimeoutException:
            return self._timeout_result()
        except httpx.HTTPError:
            return self._unavailable_result()
        return self._parse_download_response(response, request_id, allowed_domains) or self._unavailable_result()

    def health(self) -> BridgeHealthResult:
        headers = self._headers()
        if headers is None:
            return BridgeHealthResult(False, self.base_url, "unavailable", "Bridge authentication is not configured.")
        try:
            response = httpx.get(f"{self.base_url}/bridge/health/ready", headers=headers, timeout=2.0)
        except httpx.HTTPError as exc:
            return BridgeHealthResult(
                False, self.base_url, "unavailable", f"Bridge health request failed: {exc.__class__.__name__}"
            )
        parsed = self._parse_health_response(response)
        if parsed is None:
            return BridgeHealthResult(
                False, self.base_url, "unavailable", "Bridge returned an invalid readiness response."
            )
        return parsed

    def is_available(self) -> bool:
        return self.health().available

    def _parse_health_response(self, response: httpx.Response) -> BridgeHealthResult | None:
        content_type = response.headers.get("content-type", "")
        if not content_type.lower().startswith("application/json"):
            return None
        try:
            payload = response.json()
        except (TypeError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        if payload.get("contract_version") != BRIDGE_CONTRACT_VERSION or payload.get("service") != BRIDGE_SERVICE:
            return None
        status = payload.get("status")
        caps = payload.get("capabilities")
        sessions = payload.get("sessions")
        if (
            not isinstance(status, str)
            or status not in {"ready", "not_ready"}
            or not isinstance(caps, dict)
            or not isinstance(sessions, dict)
        ):
            return None
        if set(payload) != {"contract_version", "service", "status", "error_code", "capabilities", "sessions"}:
            return None
        if set(sessions) != {"configured", "ready"}:
            return None
        if set(caps) != {"browser_get_version", "fetch_interception"}:
            return None
        if not all(isinstance(value, bool) for value in caps.values()):
            return None
        configured = sessions.get("configured")
        ready = sessions.get("ready")
        if (
            isinstance(configured, bool)
            or isinstance(ready, bool)
            or not isinstance(configured, int)
            or not isinstance(ready, int)
            or not 0 <= ready <= configured <= 1000
        ):
            return None
        if status == "ready":
            if response.status_code != 200 or payload.get("error_code") is not None:
                return None
            if caps["browser_get_version"] is not True or caps["fetch_interception"] is not True:
                return None
            return BridgeHealthResult(
                True,
                self.base_url,
                "ready",
                "Bridge is ready.",
                contract_version=payload["contract_version"],
                service=payload["service"],
                capabilities=dict(caps),
                configured_sessions=configured,
                ready_sessions=ready,
            )
        if response.status_code != 503 or payload.get("error_code") != "CAPABILITY_UNAVAILABLE":
            return None
        if caps["browser_get_version"] is not False or caps["fetch_interception"] is not False:
            return None
        return BridgeHealthResult(
            False,
            self.base_url,
            "not_ready",
            "Bridge is not ready.",
            contract_version=payload["contract_version"],
            service=payload["service"],
            error_code=payload["error_code"],
            capabilities=dict(caps),
            configured_sessions=configured,
            ready_sessions=ready,
        )

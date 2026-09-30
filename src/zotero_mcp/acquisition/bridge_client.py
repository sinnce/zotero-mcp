from __future__ import annotations

import contextlib
import errno
import hashlib
import hmac
import ipaddress
import os
import re
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal
from urllib.parse import parse_qsl, urlsplit

import httpx

from . import bridge_policy

BRIDGE_SERVER_URL = "http://127.0.0.1:9870"
BRIDGE_CONTRACT_VERSION = "2.0.0"
BRIDGE_SERVICE = "pkb-browser-bridge"
_MAX_ALLOWED_DOMAINS = bridge_policy.MAX_ALLOWED_DOMAINS
_MAX_NESTED_URL_DEPTH = 2
_NESTED_SCHEMES: Final[frozenset[str]] = frozenset({"http", "https"})
_MAX_FILE_PATH_LENGTH = 4096
_MAX_ARTIFACT_BYTES = 100 * 1024 * 1024
_ARTIFACT_READ_CHUNK = 1024 * 1024

# Client-side integrity failures for a staged bridge artifact. They are not
# bridge wire codes: the client emits them after re-reading the staged file.
ARTIFACT_UNVERIFIABLE = "ARTIFACT_UNVERIFIABLE"
ARTIFACT_MISSING = "ARTIFACT_MISSING"
ARTIFACT_NOT_REGULAR = "ARTIFACT_NOT_REGULAR"
# The staged path named a different regular file when it was opened than when
# it was inspected: the bridge-owned entry was replaced in between.
ARTIFACT_REPLACED = "ARTIFACT_REPLACED"
ARTIFACT_UNREADABLE = "ARTIFACT_UNREADABLE"
ARTIFACT_SIZE_MISMATCH = "ARTIFACT_SIZE_MISMATCH"
ARTIFACT_HASH_MISMATCH = "ARTIFACT_HASH_MISMATCH"
ARTIFACT_SNAPSHOT_FAILED = "ARTIFACT_SNAPSHOT_FAILED"
# Client-side auth states, emitted before any transport call. The bridge is
# BRIDGE_NOT_CONFIGURED when no token variable, BRIDGE_SERVER_URL or explicit
# client argument names it; only then may acquisition use the direct-download
# channel. A configured bridge whose token is missing or malformed is
# BRIDGE_AUTH_INVALID.
BRIDGE_NOT_CONFIGURED = "BRIDGE_NOT_CONFIGURED"
BRIDGE_AUTH_INVALID = "BRIDGE_AUTH_INVALID"
# Client-side transport and contract failures of a configured bridge. None of
# them is a bridge wire code, and none of them permits another channel.
BRIDGE_UNREACHABLE = "BRIDGE_UNREACHABLE"
# The configured bridge URL cannot address the bridge: not http(s), no or a
# malformed host, a port outside 1..65535, userinfo, a query or a fragment.
BRIDGE_URL_INVALID = "BRIDGE_URL_INVALID"
BRIDGE_HEALTH_INVALID = "BRIDGE_HEALTH_INVALID"
BRIDGE_RESPONSE_INVALID = "BRIDGE_RESPONSE_INVALID"
_BRIDGE_TOKEN_ENV: Final[tuple[str, ...]] = ("ZOTERO_BRIDGE_TOKEN", "BRIDGE_AUTH_TOKEN", "BRIDGE_TOKEN")
# open(2) errors that mean the staged path is not a regular file: a symlink
# swapped in after lstat (O_NOFOLLOW) or a socket/device node.
_NOT_REGULAR_OPEN_ERRNOS: Final[frozenset[int]] = frozenset({errno.ELOOP, errno.ENXIO})

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


def _domain_or_none(value: str) -> str | None:
    # A usable allowlist entry for a derived list: a hostname the server's
    # isValidDomain accepts, lowercased as normalizeAllowedDomains stores it.
    return value.lower() if bridge_policy.is_valid_domain(value) else None


def _candidate_host(url: str) -> str | None:
    # The candidate host is listed even when the server will refuse it: the
    # destination check then reports the host (DOMAIN_BLOCKED) or scheme
    # problem instead of an empty-allowlist schema error.
    parsed = bridge_policy.parse_url(url) if bridge_policy.is_printable_ascii(url) else None
    if parsed is None or not parsed.host:
        return None
    host = parsed.host.lower()
    return host if len(host) <= bridge_policy.MAX_DOMAIN_LENGTH else None


def _nested_host(url: str) -> str | None:
    parsed = bridge_policy.parse_url(url) if bridge_policy.is_printable_ascii(url) else None
    if parsed is None or parsed.scheme not in _NESTED_SCHEMES or not parsed.host:
        return None
    return _domain_or_none(parsed.host)


def _derived_allowed_domains(candidate_url: object, extra_domains: str) -> list[str]:
    """Compatibility allowlist for a request that does not name one.

    The candidate host comes first, whatever its scheme, so truncation to the
    schema maximum never drops it; ``url=`` redirect targets (http or https)
    and ``BRIDGE_ALLOWED_DOMAINS`` entries follow, each only if the server
    would accept it. The derived list is then checked by
    ``bridge_policy.request_error`` like an explicit one.
    """
    domains: list[str] = []
    if not isinstance(candidate_url, str):
        return domains
    pending = [(candidate_url, 0)]
    while pending:
        url, depth = pending.pop(0)
        host = _candidate_host(url) if depth == 0 else _nested_host(url)
        if host and host not in domains:
            domains.append(host)
        if depth < _MAX_NESTED_URL_DEPTH:
            with contextlib.suppress(ValueError):
                pending.extend(
                    (value, depth + 1)
                    for key, value in parse_qsl(urlsplit(url).query, keep_blank_values=False)
                    if key.lower() == "url"
                )
    for value in extra_domains.split(","):
        domain = _domain_or_none(value.strip())
        if domain and domain not in domains:
            domains.append(domain)
    return domains[:_MAX_ALLOWED_DOMAINS]


def _is_uuid(value: object) -> bool:
    return isinstance(value, str) and bridge_policy.is_zod_uuid(value)


def _is_final_url_allowed(value: object, allowed_domains: list[str]) -> bool:
    # bridgeDownloadSuccessSchema accepts the URL; the allowlist match is a
    # client-only guard (the server only returns URLs it navigated within it).
    if not bridge_policy.is_valid_final_url(value):
        return False
    host = bridge_policy.final_url_hostname(value) if isinstance(value, str) else None
    return host is not None and bridge_policy.host_matches(host.lower(), allowed_domains)


def _is_unauthorized_envelope(response: httpx.Response, payload: dict) -> bool:
    # The server rejects a bad bearer token on every route, health included,
    # with this exact 2.0.0 error envelope.
    http_status, message = _ERROR_DETAILS["UNAUTHORIZED"]
    return (
        response.status_code == http_status
        and set(payload) == {"contract_version", "request_id", "status", "auth_state", "error_code", "message"}
        and payload.get("contract_version") == BRIDGE_CONTRACT_VERSION
        and payload.get("request_id") is None
        and payload.get("status") == "failed"
        and payload.get("auth_state") == "unchecked"
        and payload.get("error_code") == "UNAUTHORIZED"
        and payload.get("message") == message
    )


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
    except OSError as exc:
        if exc.errno in _NOT_REGULAR_OPEN_ERRNOS:
            return ARTIFACT_NOT_REGULAR, None
        return ARTIFACT_UNREADABLE, None
    snapshot_path: Path | None = None
    error_code: str | None = None
    digest = hashlib.sha256()
    total = 0
    try:
        with os.fdopen(fd, "rb") as handle:
            opened_stat = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened_stat.st_mode):
                return ARTIFACT_NOT_REGULAR, None
            if (opened_stat.st_dev, opened_stat.st_ino) != (link_stat.st_dev, link_stat.st_ino):
                return ARTIFACT_REPLACED, None
            if opened_stat.st_size != declared_size:
                return ARTIFACT_SIZE_MISMATCH, None
            sink = None
            if dest_dir is not None:
                target = dest_dir / _snapshot_name(file_path)
                sink_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                try:
                    sink_fd = os.open(target, sink_flags | getattr(os, "O_CLOEXEC", 0), 0o600)
                except OSError:
                    # The destination entry is not ours (it may already exist
                    # as a link), so it must not be unlinked below.
                    return ARTIFACT_SNAPSHOT_FAILED, None
                snapshot_path = target
                sink = os.fdopen(sink_fd, "wb")
            try:
                while True:
                    try:
                        chunk = handle.read(_ARTIFACT_READ_CHUNK)
                    except OSError:
                        error_code = ARTIFACT_UNREADABLE
                        break
                    if not chunk:
                        break
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
        # Reads are handled above; what remains are fstat or snapshot
        # write/close failures.
        error_code = ARTIFACT_SNAPSHOT_FAILED if snapshot_path is not None else ARTIFACT_UNREADABLE
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


_BASE_URL_HOSTNAME = re.compile(r"[a-z0-9-]+(?:\.[a-z0-9-]+)*\.?")
# Raised by httpx while building a request from a malformed URL; the base URL
# check refuses these first, and this belt keeps any it misses terminal.
_URL_ERRORS: Final[tuple[type[Exception], ...]] = (httpx.InvalidURL, httpx.UnsupportedProtocol, UnicodeError)


def _is_valid_base_url(value: str) -> bool:
    try:
        url = httpx.URL(value)
        raw_host = url.raw_host.decode("ascii")
        url.host  # IDNA-decodes an xn-- label; httpx raises IDNAError at send time otherwise
        port = url.port
    except Exception:  # InvalidURL, IDNAError and any other parse failure
        return False
    if url.scheme not in ("http", "https") or url.userinfo or url.query or url.fragment:
        return False
    if port is not None and not 1 <= port <= 65535:
        return False
    if ":" in raw_host:  # httpx strips the brackets of an IPv6 literal
        try:
            ipaddress.IPv6Address(raw_host)
        except ValueError:
            return False
        return True
    return _BASE_URL_HOSTNAME.fullmatch(raw_host) is not None


class BridgeClient:
    def __init__(self, base_url: str | None = None, auth_token: str | None = None):
        configured_url = base_url if base_url is not None else os.environ.get("BRIDGE_SERVER_URL", BRIDGE_SERVER_URL)
        self.base_url = configured_url.rstrip("/")
        self._base_url_valid = _is_valid_base_url(self.base_url)
        # Presence, not validity: an empty or malformed token still means the
        # operator configured the bridge, so its auth failure is terminal.
        self.configured = (
            base_url is not None
            or auth_token is not None
            or "BRIDGE_SERVER_URL" in os.environ
            or any(name in os.environ for name in _BRIDGE_TOKEN_ENV)
        )
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

    def _auth_unusable_result(self) -> BridgeDownloadResult:
        if not self.configured:
            return BridgeDownloadResult(
                status="failed",
                auth_state="unchecked",
                error_code=BRIDGE_NOT_CONFIGURED,
                message="Bridge is not configured",
            )
        return BridgeDownloadResult(
            status="failed",
            auth_state="unchecked",
            error_code=BRIDGE_AUTH_INVALID,
            message="Bridge authentication is missing or malformed",
        )

    def _url_invalid_result(self) -> BridgeDownloadResult:
        return BridgeDownloadResult(
            status="failed",
            auth_state="unchecked",
            error_code=BRIDGE_URL_INVALID,
            message="Bridge URL is malformed",
        )

    def _url_invalid_health(self) -> BridgeHealthResult:
        return BridgeHealthResult(
            False, self.base_url, "invalid_url", "Bridge URL is malformed.", error_code=BRIDGE_URL_INVALID
        )

    def _unreachable_result(self) -> BridgeDownloadResult:
        return BridgeDownloadResult(
            status="failed", auth_state="missing", error_code=BRIDGE_UNREACHABLE, message="Bridge is unreachable"
        )

    def _response_invalid_result(self) -> BridgeDownloadResult:
        return BridgeDownloadResult(
            status="failed",
            auth_state="missing",
            error_code=BRIDGE_RESPONSE_INVALID,
            message="Bridge returned an invalid download response",
        )

    def _refused_request_result(self, error_code: str) -> BridgeDownloadResult:
        # The code, status, auth state and message the server would return
        # for this payload; request_id stays None because nothing was sent.
        status, auth_state = _ERROR_STATUS.get(error_code, ("failed", "unchecked"))
        return BridgeDownloadResult(
            status=status,  # type: ignore[arg-type]
            auth_state=auth_state,  # type: ignore[arg-type]
            error_code=error_code,
            message=_ERROR_DETAILS[error_code][1],
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
                # JSON.stringify never emits an integral float, so a strict
                # int check is exact for what the server sends.
                or isinstance(payload.get("size_bytes"), bool)
                or not isinstance(payload.get("size_bytes"), int)
                or not 1 <= payload["size_bytes"] <= _MAX_ARTIFACT_BYTES
                or payload.get("content_type") != "application/pdf"
                or not _is_final_url_allowed(payload.get("final_url"), allowed_domains)
                or not bridge_policy.is_zod_datetime(payload.get("expires_at"))
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
            return self._auth_unusable_result()
        if not self._base_url_valid:
            return self._url_invalid_result()
        # An explicit allowlist is authoritative and is checked whole: it is
        # never filtered or truncated into another list.
        allowed_domains: object
        if request.allowed_domains is None:
            allowed_domains = _derived_allowed_domains(
                request.candidate_url, os.environ.get("BRIDGE_ALLOWED_DOMAINS", "")
            )
        elif isinstance(request.allowed_domains, (list, tuple)):
            allowed_domains = list(request.allowed_domains)
        else:
            allowed_domains = request.allowed_domains  # the schema check refuses it
        payload = {
            "contract_version": request.contract_version,
            "request_id": request.request_id,
            "doi": request.doi,
            "candidate_url": request.candidate_url,
            "session_name": request.session_name,
            "expected_artifact": request.expected_artifact,
            "timeout_ms": request.timeout_ms,
            "allowed_domains": allowed_domains,
        }
        # Refuse, with the server's own code, any payload the 2.0.0 server
        # would refuse before browser work (version, schema, destination).
        refused = bridge_policy.request_error(payload)
        if refused is not None:
            return self._refused_request_result(refused)
        request_id = request.request_id
        if not isinstance(request_id, str):  # unreachable: the schema requires a UUID string
            return self._refused_request_result("INVALID_REQUEST")
        # normalizeAllowedDomains lowercases every entry; the same list is
        # sent and used to check the returned final_url.
        sent_domains = [domain.lower() for domain in payload["allowed_domains"]]
        payload["allowed_domains"] = sent_domains
        try:
            response = httpx.post(
                f"{self.base_url}/bridge/download",
                json=payload,
                headers=headers,
                timeout=request.timeout_ms / 1000 + 5,
            )
        except _URL_ERRORS:
            return self._url_invalid_result()
        except httpx.TimeoutException:
            return self._timeout_result()
        except httpx.HTTPError:
            return self._unreachable_result()
        return self._parse_download_response(response, request_id, sent_domains) or self._response_invalid_result()

    def health(self) -> BridgeHealthResult:
        headers = self._headers()
        if headers is None:
            if not self.configured:
                return BridgeHealthResult(
                    False,
                    self.base_url,
                    "not_configured",
                    "Bridge is not configured.",
                    error_code=BRIDGE_NOT_CONFIGURED,
                )
            return BridgeHealthResult(
                False,
                self.base_url,
                "unauthorized",
                "Bridge authentication is missing or malformed.",
                error_code=BRIDGE_AUTH_INVALID,
            )
        if not self._base_url_valid:
            return self._url_invalid_health()
        try:
            response = httpx.get(f"{self.base_url}/bridge/health/ready", headers=headers, timeout=2.0)
        except _URL_ERRORS:
            return self._url_invalid_health()
        except httpx.HTTPError as exc:
            return BridgeHealthResult(
                False,
                self.base_url,
                "unreachable",
                f"Bridge health request failed: {exc.__class__.__name__}",
                error_code=BRIDGE_UNREACHABLE,
            )
        parsed = self._parse_health_response(response)
        if parsed is None:
            return BridgeHealthResult(
                False,
                self.base_url,
                "invalid",
                "Bridge returned an invalid readiness response.",
                error_code=BRIDGE_HEALTH_INVALID,
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
        if _is_unauthorized_envelope(response, payload):
            return BridgeHealthResult(
                False,
                self.base_url,
                "unauthorized",
                "Bridge rejected the configured authentication.",
                contract_version=payload["contract_version"],
                error_code="UNAUTHORIZED",
            )
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

"""Client mirror of the browser-bridge 2.0.0 request policy.

The bridge server (``packages/opencode-deep-research/lib`` in the deep-research
repository) is the policy authority. This module reproduces, rule for rule and
in the same order, the checks it applies to a download request before any
browser work starts, so the client can refuse a request with the exact error
code the server would return without sending it:

1. ``classifyVersion`` in ``bridge-server.ts`` (``LEGACY_DISABLED``,
   ``UNSUPPORTED_CONTRACT_VERSION``);
2. ``bridgeDownloadRequestSchema`` in ``bridge-v2-contract.ts``
   (``INVALID_REQUEST``);
3. ``validateBridgeDestination`` in ``bridge-v2-policy.ts`` (``INVALID_REQUEST``,
   ``URL_SCHEME_BLOCKED``, ``DOMAIN_BLOCKED``).

URLs are parsed with a WHATWG URL parser subset, because the server uses
``new URL()``; Python's ``urllib.parse`` disagrees with it on many inputs.
A domain that is not ASCII after percent-decoding, or that has an ``xn--``
label, goes through ``bridge_idna.to_ascii``, the client mirror of the
UTS #46 ToASCII step Bun applies (ICU 75.1, Unicode 15.1), so an
internationalized host is accepted, refused or rejected as unparsable
exactly as on the server. Allowlist entries are not URL hosts and are
matched exactly, ``xn--`` labels included.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Final

from . import bridge_idna

BRIDGE_CONTRACT_VERSION: Final = "2.0.0"

_PRINTABLE_ASCII = re.compile(r"[\x21-\x7e]+")
_LONE_PERCENT = re.compile(r"%(?![0-9a-fA-F]{2})")
_LEGACY_VERSION = re.compile(r"1(?:\.[0-9]+){1,2}")
# zod 4.1.8 z.string().uuid() (regexes.uuid() with no version).
_ZOD_UUID = re.compile(
    r"(?:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|"
    r"00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)"
)
_SESSION_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")
_DOMAIN_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?")
# Node/Bun net.isIPv4: dotted quad without leading zeros.
_NODE_IPV4 = re.compile(
    r"(?:(?:25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9][0-9]|[0-9])\.){3}(?:25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9][0-9]|[0-9])"
)
# ECMAScript String.prototype.trim: WhiteSpace plus LineTerminator. Python's
# str.strip() differs (it strips U+001C..U+001F and U+0085, keeps U+FEFF).
_JS_TRIM_CHARS: Final = (
    "\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000\ufeff"
)

MAX_DOI_CODE_POINTS: Final = 512
MAX_URL_LENGTH: Final = 2048
MAX_DOMAIN_LENGTH: Final = 253
MAX_ALLOWED_DOMAINS: Final = 32
MAX_BODY_BYTES: Final = 16_384
MIN_TIMEOUT_MS: Final = 1000
MAX_TIMEOUT_MS: Final = 300000

_SPECIAL_SCHEMES: Final[dict[str, int | None]] = {
    "ftp": 21,
    "file": None,
    "http": 80,
    "https": 443,
    "ws": 80,
    "wss": 443,
}
_FORBIDDEN_HOST_CODE_POINTS: Final = frozenset("\x00\t\n\r #/:<>?@[\\]^|")
_FORBIDDEN_DOMAIN_CODE_POINTS: Final = _FORBIDDEN_HOST_CODE_POINTS | frozenset(
    [chr(c) for c in range(0x20)] + ["%", "\x7f"]
)
_ASCII_ALPHA: Final = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")
_ASCII_DIGITS: Final = frozenset("0123456789")
_ASCII_HEX: Final = frozenset("0123456789abcdefABCDEF")
_SCHEME_CHARS: Final = _ASCII_ALPHA | _ASCII_DIGITS | frozenset("+-.")


class _Failure(Exception):
    """WHATWG URL parse failure (``new URL()`` throws)."""


@dataclass(frozen=True)
class WhatwgUrl:
    scheme: str
    username: str
    password: str
    # Serialized host: a lowercase ASCII domain, a dotted IPv4 address, an
    # IPv6 literal in brackets, an opaque host, "" or None (no host).
    host: str | None
    port: int | None
    fragment: str | None


def js_trim_differs(value: str) -> bool:
    """``value !== value.trim()`` in ECMAScript."""
    return value != value.strip(_JS_TRIM_CHARS)


def has_lone_percent(value: str) -> bool:
    return _LONE_PERCENT.search(value) is not None


def is_printable_ascii(value: object) -> bool:
    return isinstance(value, str) and _PRINTABLE_ASCII.fullmatch(value) is not None


def parse_url(value: str) -> WhatwgUrl | None:
    """Parse a printable-ASCII absolute URL like WHATWG ``new URL(value)``.

    Returns ``None`` where ``new URL()`` throws. Input outside printable ASCII
    is not supported and returns ``None``.
    """
    if not is_printable_ascii(value):
        return None
    try:
        return _parse(value)
    except _Failure:
        return None


def _parse(value: str) -> WhatwgUrl:
    if value[0] not in _ASCII_ALPHA:
        raise _Failure
    index = 1
    while index < len(value) and value[index] in _SCHEME_CHARS:
        index += 1
    if index == len(value) or value[index] != ":":
        raise _Failure
    scheme = value[:index].lower()
    rest = value[index + 1 :]
    if scheme == "file":
        return _parse_file(rest)
    if scheme in _SPECIAL_SCHEMES:
        rest = rest.lstrip("/\\")
        authority, remainder = _split_authority(rest, special=True)
        return _parse_authority(scheme, authority, remainder, special=True)
    if rest.startswith("//"):
        authority, remainder = _split_authority(rest[2:], special=False)
        return _parse_authority(scheme, authority, remainder, special=False)
    return WhatwgUrl(scheme, "", "", None, None, _fragment(rest))


def _split_authority(value: str, *, special: bool) -> tuple[str, str]:
    delimiters = "/?#\\" if special else "/?#"
    for position, char in enumerate(value):
        if char in delimiters:
            return value[:position], value[position:]
    return value, ""


def _fragment(remainder: str) -> str | None:
    _, has_hash, fragment = remainder.partition("#")
    return fragment if has_hash else None


def _parse_authority(scheme: str, authority: str, remainder: str, *, special: bool) -> WhatwgUrl:
    username = password = buffer = ""
    at_sign_seen = password_token_seen = False
    for char in authority:
        if char != "@":
            buffer += char
            continue
        if at_sign_seen:
            buffer = "%40" + buffer
        at_sign_seen = True
        for code_point in buffer:
            if code_point == ":" and not password_token_seen:
                password_token_seen = True
            elif password_token_seen:
                password += code_point
            else:
                username += code_point
        buffer = ""
    if at_sign_seen and buffer == "":
        raise _Failure

    host_buffer, port_buffer, has_port = buffer, "", False
    inside_brackets = False
    for position, char in enumerate(buffer):
        if char == ":" and not inside_brackets:
            host_buffer, port_buffer, has_port = buffer[:position], buffer[position + 1 :], True
            break
        if char == "[":
            inside_brackets = True
        elif char == "]":
            inside_brackets = False
    if has_port and host_buffer == "":
        raise _Failure
    if special and host_buffer == "":
        raise _Failure
    host = _parse_host(host_buffer, opaque=not special) if host_buffer else ""

    port = None
    if port_buffer:
        if any(char not in _ASCII_DIGITS for char in port_buffer):
            raise _Failure
        port = int(port_buffer)
        if port > 65535:
            raise _Failure
        if port == _SPECIAL_SCHEMES.get(scheme):
            port = None
    return WhatwgUrl(scheme, username, password, host, port, _fragment(remainder))


def _parse_file(rest: str) -> WhatwgUrl:
    host = None
    remainder = rest
    if rest[:1] in ("/", "\\") and rest[1:2] in ("/", "\\"):
        host_buffer, remainder = _split_authority(rest[2:], special=True)
        is_drive_letter = len(host_buffer) == 2 and host_buffer[0] in _ASCII_ALPHA and host_buffer[1] in (":", "|")
        if is_drive_letter:
            remainder = host_buffer + remainder
        elif host_buffer == "":
            host = ""
        else:
            host = _parse_host(host_buffer, opaque=False)
            if host == "localhost":
                host = ""
    return WhatwgUrl("file", "", "", host, None, _fragment(remainder))


def _parse_host(value: str, *, opaque: bool) -> str:
    if value.startswith("["):
        if not value.endswith("]"):
            raise _Failure
        _parse_ipv6(value[1:-1])
        return value.lower()
    if opaque:
        if any(char in _FORBIDDEN_HOST_CODE_POINTS for char in value):
            raise _Failure
        return value
    try:
        domain = _percent_decode(value).decode("utf-8")
    except UnicodeDecodeError:
        # Invalid UTF-8 decodes to U+FFFD, which UTS #46 disallows.
        raise _Failure from None
    # WebKit's domainToASCII: ICU runs only for a non-ASCII domain or one
    # with a label starting "xn--" (any case); otherwise it lowercases.
    if domain.isascii() and not any(label[:4].lower() == "xn--" for label in domain.split(".")):
        ascii_domain = domain.lower()
    else:
        converted = bridge_idna.to_ascii(domain)
        if converted is None:
            raise _Failure
        ascii_domain = converted
    if any(char in _FORBIDDEN_DOMAIN_CODE_POINTS for char in ascii_domain):
        raise _Failure
    if _ends_in_number(ascii_domain):
        return _parse_ipv4(ascii_domain)
    return ascii_domain


def _percent_decode(value: str) -> bytes:
    raw = value.encode("utf-8")
    output = bytearray()
    index = 0
    while index < len(raw):
        byte = raw[index]
        if (
            byte == 0x25
            and index + 2 < len(raw)
            and chr(raw[index + 1]) in _ASCII_HEX
            and chr(raw[index + 2]) in _ASCII_HEX
        ):
            output.append(int(raw[index + 1 : index + 3], 16))
            index += 3
        else:
            output.append(byte)
            index += 1
    return bytes(output)


def _parse_ipv4_number(value: str) -> int | None:
    if value == "":
        return None
    radix = 10
    if len(value) >= 2 and value[:2] in ("0x", "0X"):
        value, radix = value[2:], 16
    elif len(value) >= 2 and value[0] == "0":
        value, radix = value[1:], 8
    if value == "":
        return 0
    digits = {10: _ASCII_DIGITS, 16: _ASCII_HEX, 8: frozenset("01234567")}[radix]
    if any(char not in digits for char in value):
        return None
    return int(value, radix)


def _ends_in_number(value: str) -> bool:
    parts = value.split(".")
    if parts[-1] == "":
        if len(parts) == 1:
            return False
        parts.pop()
    last = parts[-1]
    if last != "" and all(char in _ASCII_DIGITS for char in last):
        return True
    return _parse_ipv4_number(last) is not None


def _parse_ipv4(value: str) -> str:
    parts = value.split(".")
    if parts[-1] == "" and len(parts) > 1:
        parts.pop()
    if len(parts) > 4:
        raise _Failure
    numbers: list[int] = []
    for part in parts:
        number = _parse_ipv4_number(part)
        if number is None:
            raise _Failure
        numbers.append(number)
    if any(number > 255 for number in numbers[:-1]):
        raise _Failure
    if numbers[-1] >= 256 ** (5 - len(numbers)):
        raise _Failure
    address = numbers[-1]
    for position, number in enumerate(numbers[:-1]):
        address += number * 256 ** (3 - position)
    return ".".join(str((address >> shift) & 0xFF) for shift in (24, 16, 8, 0))


def _parse_ipv6(value: str) -> None:
    # WHATWG IPv6 parser; only acceptance matters to the policy.
    pieces = [0] * 8
    piece_index = 0
    compress: int | None = None
    pointer = 0

    def at(position: int) -> str | None:
        return value[position] if position < len(value) else None

    if at(pointer) == ":":
        if at(pointer + 1) != ":":
            raise _Failure
        pointer += 2
        piece_index += 1
        compress = piece_index
    while at(pointer) is not None:
        if piece_index == 8:
            raise _Failure
        if at(pointer) == ":":
            if compress is not None:
                raise _Failure
            pointer += 1
            piece_index += 1
            compress = piece_index
            continue
        piece = length = 0
        while length < 4 and at(pointer) is not None and at(pointer) in _ASCII_HEX:
            piece = piece * 0x10 + int(value[pointer], 16)
            pointer += 1
            length += 1
        if at(pointer) == ".":
            if length == 0:
                raise _Failure
            pointer -= length
            if piece_index > 6:
                raise _Failure
            numbers_seen = 0
            while at(pointer) is not None:
                ipv4_piece: int | None = None
                if numbers_seen > 0:
                    if at(pointer) == "." and numbers_seen < 4:
                        pointer += 1
                    else:
                        raise _Failure
                if at(pointer) is None or at(pointer) not in _ASCII_DIGITS:
                    raise _Failure
                while at(pointer) is not None and at(pointer) in _ASCII_DIGITS:
                    number = int(value[pointer])
                    if ipv4_piece is None:
                        ipv4_piece = number
                    elif ipv4_piece == 0:
                        raise _Failure
                    else:
                        ipv4_piece = ipv4_piece * 10 + number
                    if ipv4_piece > 255:
                        raise _Failure
                    pointer += 1
                pieces[piece_index] = pieces[piece_index] * 0x100 + (ipv4_piece or 0)
                numbers_seen += 1
                if numbers_seen in (2, 4):
                    piece_index += 1
            if numbers_seen != 4:
                raise _Failure
            break
        if at(pointer) == ":":
            pointer += 1
            if at(pointer) is None:
                raise _Failure
        elif at(pointer) is not None:
            raise _Failure
        pieces[piece_index] = piece
        piece_index += 1
    if compress is None and piece_index != 8:
        raise _Failure


def _is_node_ip(value: str) -> bool:
    # net.isIP() on a WHATWG hostname: IPv6 hostnames keep their brackets
    # (isIP 0) and fail isValidDomain on ":" instead.
    return _NODE_IPV4.fullmatch(value) is not None


def is_valid_domain(value: object) -> bool:
    """``isValidDomain`` in ``bridge-v2-policy.ts``.

    Its final ``domainToASCII(lower) === lower`` check is always true here:
    Bun's ``node:url`` ``domainToASCII`` returns a lowercase string of ASCII
    letters, digits, hyphens and dots unchanged, including ``xn--`` labels and
    labels that look numeric (``example.123``, ``a.0x1f``). Only ``isIP``
    refuses a dotted quad. Hostnames reaching this check from ``new URL()``
    have already been through the WHATWG host parser.
    """
    if not isinstance(value, str) or not 1 <= len(value) <= MAX_DOMAIN_LENGTH:
        return False
    if not is_printable_ascii(value) or value.endswith("."):
        return False
    if ":" in value or "/" in value or "*" in value:
        return False
    if _is_node_ip(value):
        return False
    labels = value.split(".")
    if len(labels) < 2:
        return False
    return all(len(label) <= 63 and _DOMAIN_LABEL.fullmatch(label) is not None for label in labels)


def is_valid_final_hostname(value: str) -> bool:
    """``isValidFinalHostname`` in ``bridge-v2-contract.ts``."""
    if len(value) > MAX_DOMAIN_LENGTH or value.endswith("."):
        return False
    labels = value.split(".")
    return len(labels) >= 2 and all(
        1 <= len(label) <= 63 and _DOMAIN_LABEL.fullmatch(label) is not None for label in labels
    )


def host_matches(hostname: str, allowed_domains: list[str]) -> bool:
    return any(hostname == domain or hostname.endswith(f".{domain}") for domain in allowed_domains)


# zod 4.1.8 z.string().datetime({ offset: false }): regexes.datetime() with no
# precision and no local time. JavaScript \d is ASCII-only, hence re.ASCII.
_ZOD_DATE = (
    r"(?:(?:\d\d[2468][048]|\d\d[13579][26]|\d\d0[48]|[02468][048]00|[13579][26]00)-02-29|"
    r"\d{4}-(?:(?:0[13578]|1[02])-(?:0[1-9]|[12]\d|3[01])|(?:0[469]|11)-(?:0[1-9]|[12]\d|30)|"
    r"(?:02)-(?:0[1-9]|1\d|2[0-8])))"
)
_ZOD_DATETIME = re.compile(
    _ZOD_DATE + r"T(?:(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d(?:\.\d+)?)?(?:Z))",
    re.ASCII,
)


def is_zod_uuid(value: str) -> bool:
    """``bridgeRequestIdSchema`` (``z.string().uuid()``)."""
    return _ZOD_UUID.fullmatch(value) is not None


def is_zod_datetime(value: object) -> bool:
    """``expires_at`` in ``bridgeDownloadSuccessSchema``."""
    return isinstance(value, str) and _ZOD_DATETIME.fullmatch(value) is not None


def is_json_int(value: object, minimum: int, maximum: int) -> bool:
    """A value JSON-encodes to a number that zod ``z.number().int()`` accepts in range."""
    if isinstance(value, bool):
        return False
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            return False
    elif not isinstance(value, int):
        return False
    return minimum <= value <= maximum


def _encoded_size(payload: dict) -> int | None:
    # The same encoding httpx applies to json=; a payload it cannot encode
    # (None) can never reach the server.
    try:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError):
        return None
    return len(body)


def _matches_request_schema(payload: dict) -> bool:
    # bridgeDownloadRequestSchema (strict object). The client always builds
    # exactly these keys; every mismatch here is INVALID_REQUEST.
    doi = payload.get("doi")
    candidate_url = payload.get("candidate_url")
    session_name = payload.get("session_name")
    allowed_domains = payload.get("allowed_domains")
    request_id = payload.get("request_id")
    return (
        isinstance(request_id, str)
        and _ZOD_UUID.fullmatch(request_id) is not None
        and isinstance(doi, str)
        and 1 <= len(doi) <= MAX_DOI_CODE_POINTS
        and isinstance(candidate_url, str)
        and 1 <= len(candidate_url) <= MAX_URL_LENGTH
        and is_printable_ascii(candidate_url)
        and isinstance(session_name, str)
        and _SESSION_NAME.fullmatch(session_name) is not None
        and payload.get("expected_artifact") == "pdf"
        and isinstance(allowed_domains, (list, tuple))
        and 1 <= len(allowed_domains) <= MAX_ALLOWED_DOMAINS
        and all(
            isinstance(domain, str) and len(domain) <= MAX_DOMAIN_LENGTH and is_printable_ascii(domain)
            for domain in allowed_domains
        )
        and is_json_int(payload.get("timeout_ms"), MIN_TIMEOUT_MS, MAX_TIMEOUT_MS)
    )


def destination_error(doi: str, candidate_url: str, allowed_domains: list[str] | tuple[str, ...]) -> str | None:
    """``validateBridgeDestination`` in ``bridge-v2-policy.ts``, same order and codes."""
    if js_trim_differs(doi):
        return "INVALID_REQUEST"
    if has_lone_percent(candidate_url):
        return "INVALID_REQUEST"
    parsed = parse_url(candidate_url)
    if parsed is None:
        return "INVALID_REQUEST"
    if parsed.scheme != "https":
        return "URL_SCHEME_BLOCKED"
    if parsed.username or parsed.password or parsed.fragment:
        return "INVALID_REQUEST"
    hostname = (parsed.host or "").lower()
    if _is_node_ip(hostname) or not is_valid_domain(hostname):
        return "DOMAIN_BLOCKED"
    # normalizeAllowedDomains
    normalized: list[str] = []
    for domain in allowed_domains:
        if not is_valid_domain(domain):
            return "DOMAIN_BLOCKED"
        lowered = domain.lower()
        if lowered in normalized:
            return "DOMAIN_BLOCKED"
        normalized.append(lowered)
    if not host_matches(hostname, normalized):
        return "DOMAIN_BLOCKED"
    return None


def request_error(payload: dict) -> str | None:
    """The code the 2.0.0 server returns for ``payload`` before browser work.

    ``None`` means the payload passes every check the client can evaluate;
    the session registry, DNS and readiness checks that follow stay on the
    server. A payload that cannot be JSON-encoded is ``INVALID_REQUEST``.
    """
    size = _encoded_size(payload)
    if size is None:
        return "INVALID_REQUEST"
    # The declared Content-Length check and bounded body read in
    # bridge-server.ts run before JSON.parse and every payload check.
    if size > MAX_BODY_BYTES:
        return "REQUEST_TOO_LARGE"
    # classifyVersion in bridge-server.ts. The client always sends the key,
    # so the versionless-legacy branch cannot apply.
    version = payload.get("contract_version")
    if isinstance(version, str) and _LEGACY_VERSION.fullmatch(version):
        return "LEGACY_DISABLED"
    if not isinstance(version, str) or version != BRIDGE_CONTRACT_VERSION:
        return "UNSUPPORTED_CONTRACT_VERSION"
    if not _matches_request_schema(payload):
        return "INVALID_REQUEST"
    return destination_error(payload["doi"], payload["candidate_url"], payload["allowed_domains"])


def is_valid_final_url(value: object) -> bool:
    """``final_url`` in ``bridgeDownloadSuccessSchema`` (wire pattern and ``isSafeFinalUrl``)."""
    if not isinstance(value, str) or not 1 <= len(value) <= MAX_URL_LENGTH:
        return False
    if not re.fullmatch(r"[Hh][Tt][Tt][Pp][Ss]://[\x21-\x7e]+", value) or has_lone_percent(value):
        return False
    parsed = parse_url(value)
    if parsed is None:
        return False
    hostname = parsed.host or ""
    if hostname.startswith("["):
        return False  # a bracketed hostname is an IPv6 literal: isIP() !== 0
    return (
        parsed.scheme == "https"
        and parsed.username == ""
        and parsed.password == ""
        and not parsed.fragment
        and not _is_node_ip(hostname)
        and is_valid_final_hostname(hostname)
    )


def final_url_hostname(value: str) -> str | None:
    parsed = parse_url(value)
    return None if parsed is None else parsed.host

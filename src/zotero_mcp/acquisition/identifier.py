"""Identifier normalization for paper acquisition pipeline."""

from __future__ import annotations
import re
from dataclasses import dataclass


@dataclass
class CanonicalId:
    type: str  # doi | arxiv | pmid | url
    value: str
    version: int | None = None
    raw_input: str = ""


class NormalizationError(ValueError):
    """Raised when input cannot be parsed as a known identifier."""


_DOI_BARE = re.compile(r"^10\.\d{4,9}/\S+")
_DOI_URL = re.compile(r"doi\.org/(10\.\d{4,9}/[^\s?#]+)", re.IGNORECASE)
_DOI_PREFIX = re.compile(r"^doi:(10\.\d{4,9}/\S+)", re.IGNORECASE)

_ARXIV_NEW = re.compile(r"^(\d{4}\.\d{4,5})(v(\d+))?$")
_ARXIV_OLD = re.compile(r"^([a-z\-]+/\d{7})(v(\d+))?$", re.IGNORECASE)
_ARXIV_URL = re.compile(
    r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5}(?:v\d+)?|[a-z\-]+/\d{7}(?:v\d+)?)(?:\.pdf)?",
    re.IGNORECASE,
)
_ARXIV_PREFIX = re.compile(r"^arxiv:(.+)$", re.IGNORECASE)

_PMID = re.compile(r"^(?:PMID:?|pmid:?)(\d+)$", re.IGNORECASE)


def normalize(raw: str | None) -> CanonicalId:
    """Parse raw input into a CanonicalId."""
    if not raw or not str(raw).strip():
        raise NormalizationError(f"Cannot normalize empty input: {raw!r}")

    s = str(raw).strip()

    # DOI URL: https://doi.org/...
    m = _DOI_URL.search(s)
    if m:
        val = m.group(1).rstrip(".,);]")
        return CanonicalId(type="doi", value=val, raw_input=raw)

    # DOI prefix: doi:10.xxx
    m = _DOI_PREFIX.match(s)
    if m:
        return CanonicalId(type="doi", value=m.group(1), raw_input=raw)

    # Bare DOI: 10.xxx/...
    if _DOI_BARE.match(s):
        return CanonicalId(type="doi", value=s.rstrip(".,);]"), raw_input=raw)

    # arXiv prefix: arxiv:...
    m = _ARXIV_PREFIX.match(s)
    if m:
        s = m.group(1).strip()

    # arXiv URL
    m = _ARXIV_URL.search(str(raw))
    if m:
        arxiv_raw = m.group(1)
        return _parse_arxiv_id(arxiv_raw, raw)

    # arXiv new format: YYMM.NNNNN[vN]
    m = _ARXIV_NEW.match(s)
    if m:
        ver = int(m.group(3)) if m.group(3) else None
        return CanonicalId(type="arxiv", value=m.group(1), version=ver, raw_input=raw)

    # arXiv old format: category/NNNNNNN[vN]
    m = _ARXIV_OLD.match(s)
    if m:
        ver = int(m.group(3)) if m.group(3) else None
        return CanonicalId(type="arxiv", value=m.group(1), version=ver, raw_input=raw)

    # PMID
    m = _PMID.match(s)
    if m:
        return CanonicalId(type="pmid", value=m.group(1), raw_input=raw)

    # URL fallback: any http/https URL
    if s.lower().startswith("http://") or s.lower().startswith("https://"):
        return CanonicalId(type="url", value=s, raw_input=raw)

    raise NormalizationError(
        f"Cannot parse {raw!r} as DOI, arXiv ID, PMID, or URL. "
        "Expected formats: '10.xxx/yyy', 'https://doi.org/...', '2301.00001', 'PMID:12345678', 'https://...'"
    )


def _parse_arxiv_id(arxiv_raw: str, raw_input: str) -> CanonicalId:
    """Parse an already-extracted arXiv ID string."""
    m = _ARXIV_NEW.match(arxiv_raw)
    if m:
        ver = int(m.group(3)) if m.group(3) else None
        return CanonicalId(type="arxiv", value=m.group(1), version=ver, raw_input=raw_input)
    m = _ARXIV_OLD.match(arxiv_raw)
    if m:
        ver = int(m.group(3)) if m.group(3) else None
        return CanonicalId(type="arxiv", value=m.group(1), version=ver, raw_input=raw_input)
    return CanonicalId(type="arxiv", value=arxiv_raw, raw_input=raw_input)

"""Orchestration logic for resolve_paper_access tool."""

from __future__ import annotations
import logging
import re
from urllib.parse import urlparse

import httpx

from .identifier import CanonicalId, NormalizationError, normalize
from .unpaywall import UnpaywallClient
from .arxiv import ArxivResolver
from .institutional import InstitutionalResolver
from .config import AcquisitionConfig, load_acquisition_config
from .types import AccessResolution


logger = logging.getLogger(__name__)

_IEEE_HOSTS = {"ieeexplore.ieee.org", "www.ieeexplore.ieee.org"}
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
_CITATION_DOI_META = re.compile(
    r'<meta[^>]+name=["\']citation_doi["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_IEEE_METADATA_BLOB = re.compile(r"xplGlobal\.document\.metadata\s*=\s*(\{.*?\});", re.DOTALL)
_DOI_IN_JSON = re.compile(r'"doi"\s*:\s*"(10\.\d{4,9}/[^"\\]+)"', re.IGNORECASE)


def _is_ieee_xplore_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.netloc.lower() in _IEEE_HOSTS


def _extract_doi_from_ieee_html(html: str) -> str | None:
    meta_match = _CITATION_DOI_META.search(html)
    if meta_match:
        return meta_match.group(1).strip()

    blob_match = _IEEE_METADATA_BLOB.search(html)
    if blob_match:
        blob_doi_match = _DOI_IN_JSON.search(blob_match.group(1))
        if blob_doi_match:
            return blob_doi_match.group(1).strip()

    json_match = _DOI_IN_JSON.search(html)
    if json_match:
        return json_match.group(1).strip()

    return None


async def _upgrade_known_publisher_url(canonical: CanonicalId) -> CanonicalId:
    if canonical.type != "url" or not _is_ieee_xplore_url(canonical.value):
        return canonical

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, headers=_BROWSER_HEADERS) as client:
            response = await client.get(canonical.value)
        if response.status_code >= 400:
            return canonical
        doi = _extract_doi_from_ieee_html(response.text)
        if not doi:
            return canonical
        upgraded = normalize(doi)
        return CanonicalId(
            type=upgraded.type, value=upgraded.value, version=upgraded.version, raw_input=canonical.raw_input
        )
    except Exception as exc:
        logger.debug("Failed to upgrade publisher URL %s: %s", canonical.value, exc)
        return canonical


async def resolve_access(
    identifier: str,
    config: AcquisitionConfig | None = None,
) -> AccessResolution:
    """Resolve a paper identifier to access locations. Never raises."""
    if config is None:
        config = load_acquisition_config()

    try:
        canonical = normalize(identifier)
    except NormalizationError as e:
        return AccessResolution(
            identifier_type="unknown",
            identifier_value=identifier,
            metadata={"error": str(e)},
        )

    canonical = await _upgrade_known_publisher_url(canonical)

    locations = []
    best_location = None
    metadata: dict = {"identifier_type": canonical.type, "identifier_value": canonical.value}

    if canonical.type == "doi":
        client = UnpaywallClient(email=config.unpaywall_email)
        result = await client.resolve(canonical.value)
        locations.extend(result.locations)
        best_location = result.best_location
        metadata.update(result.metadata)

    elif canonical.type == "arxiv":
        resolver = ArxivResolver()
        result = await resolver.resolve(canonical.value)
        locations.extend(result.locations)
        best_location = result.best_location
        metadata.update(result.metadata)

    elif canonical.type == "url":
        from .types import AccessLocation

        locations.append(AccessLocation(url=canonical.value, access_method="direct"))
        best_location = locations[0]

    if canonical.type == "doi" and config.institutional_access.enabled:
        inst = InstitutionalResolver(config)
        inst_result = await inst.resolve(canonical.value, metadata)
        locations.extend(inst_result.locations)
        if best_location is None and inst_result.best_location:
            best_location = inst_result.best_location

    return AccessResolution(
        identifier_type=canonical.type,
        identifier_value=canonical.value,
        locations=locations,
        best_location=best_location,
        metadata=metadata,
    )

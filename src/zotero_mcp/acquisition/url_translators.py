from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from .identifier import CanonicalId, normalize

logger = logging.getLogger(__name__)

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


class URLTranslator(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    def supports(self, canonical: CanonicalId) -> bool:
        pass

    @abstractmethod
    async def translate(self, canonical: CanonicalId) -> CanonicalId:
        pass


class URLTranslatorRegistry:
    def __init__(self):
        self._translators: list[URLTranslator] = []

    def register(self, translator: URLTranslator) -> None:
        self._translators.append(translator)

    async def translate(self, canonical: CanonicalId) -> CanonicalId:
        for translator in self._translators:
            if translator.supports(canonical):
                return await translator.translate(canonical)
        return canonical


_IEEE_HOSTS = {"ieeexplore.ieee.org", "www.ieeexplore.ieee.org"}
_CITATION_DOI_META = re.compile(
    r'<meta[^>]+name=["\']citation_doi["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_IEEE_METADATA_BLOB = re.compile(r"xplGlobal\.document\.metadata\s*=\s*(\{.*?\});", re.DOTALL)
_DOI_IN_JSON = re.compile(r'"doi"\s*:\s*"(10\.\d{4,9}/[^"\\]+)"', re.IGNORECASE)


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


@dataclass
class IEEEXploreTranslator(URLTranslator):
    @property
    def name(self) -> str:
        return "ieee-xplore"

    def supports(self, canonical: CanonicalId) -> bool:
        if canonical.type != "url":
            return False
        parsed = urlparse(canonical.value)
        return parsed.netloc.lower() in _IEEE_HOSTS

    async def translate(self, canonical: CanonicalId) -> CanonicalId:
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
                type=upgraded.type,
                value=upgraded.value,
                version=upgraded.version,
                raw_input=canonical.raw_input,
            )
        except Exception as exc:
            logger.debug("Failed to translate IEEE Xplore URL %s: %s", canonical.value, exc)
            return canonical


def build_default_url_translator_registry() -> URLTranslatorRegistry:
    registry = URLTranslatorRegistry()
    registry.register(IEEEXploreTranslator())
    return registry

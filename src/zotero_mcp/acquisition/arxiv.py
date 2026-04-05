from __future__ import annotations
import asyncio
import logging
import re
import time
import xml.etree.ElementTree as ET

from .http_client import fetch_with_retry
from .types import AccessLocation, AccessResolution

logger = logging.getLogger(__name__)
_API = "https://export.arxiv.org/api/query?id_list={id}"
_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


class ArxivResolver:
    def __init__(self, rate_limit_seconds: float = 3.0):
        self._rate_limit = rate_limit_seconds
        self._last_call: float = 0.0

    def pdf_url(self, arxiv_id: str) -> str:
        return f"https://arxiv.org/pdf/{arxiv_id}.pdf"

    def source_url(self, arxiv_id: str) -> str:
        return f"https://arxiv.org/e-print/{arxiv_id}"

    async def _fetch(self, arxiv_id: str) -> AccessResolution:
        url = _API.format(id=arxiv_id)
        result = await fetch_with_retry(url, max_retries=1, retry_delay=0.0)
        if not result.success:
            return AccessResolution(identifier_type="arxiv", identifier_value=arxiv_id)
        return self._parse(arxiv_id, result.content.decode("utf-8", errors="ignore"))

    def _parse(self, arxiv_id: str, xml_text: str) -> AccessResolution:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return AccessResolution(identifier_type="arxiv", identifier_value=arxiv_id)

        entry = root.find("atom:entry", _NS)
        if entry is None:
            return AccessResolution(identifier_type="arxiv", identifier_value=arxiv_id)

        title_el = entry.find("atom:title", _NS)
        summary_el = entry.find("atom:summary", _NS)
        authors = [
            a.find("atom:name", _NS).text
            for a in entry.findall("atom:author", _NS)
            if a.find("atom:name", _NS) is not None
        ]

        # arXiv entry IDs include version suffix e.g. "abs/2301.00001v1" → strip to "2301.00001"
        raw_id_el = entry.find("atom:id", _NS)
        clean_id = arxiv_id
        if raw_id_el is not None and raw_id_el.text:
            m = re.search(r"abs/(.+?)(?:v\d+)?$", raw_id_el.text)
            if m:
                clean_id = m.group(1)

        pdf_url = self.pdf_url(clean_id)
        location = AccessLocation(url=pdf_url, access_method="oa", version="submittedVersion")

        metadata: dict = {}
        if title_el is not None and title_el.text:
            metadata["title"] = title_el.text.strip()
        if summary_el is not None and summary_el.text:
            metadata["abstract"] = summary_el.text.strip()
        if authors:
            metadata["authors"] = authors

        return AccessResolution(
            identifier_type="arxiv",
            identifier_value=clean_id,
            locations=[location],
            best_location=location,
            metadata=metadata,
        )

    async def resolve(self, arxiv_id: str) -> AccessResolution:
        """Never raises. Rate-limits calls to the arXiv API."""
        now = time.monotonic()
        elapsed = now - self._last_call
        if self._last_call > 0 and elapsed < self._rate_limit:
            await asyncio.sleep(self._rate_limit - elapsed)
        self._last_call = time.monotonic()

        try:
            return await self._fetch(arxiv_id)
        except Exception as e:
            logger.debug("arXiv resolve failed: %s", e)
            return AccessResolution(identifier_type="arxiv", identifier_value=arxiv_id)

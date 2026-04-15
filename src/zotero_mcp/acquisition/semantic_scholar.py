from __future__ import annotations
import json
import logging

import httpx

from .types import AccessResolution, AccessLocation
from .config import AcquisitionConfig

logger = logging.getLogger(__name__)

_S2_API_BASE = "https://api.semanticscholar.org/graph/v1/paper"
_S2_FIELDS = "title,authors,abstract,isOpenAccess,openAccessPdf"


class SemanticScholarResolver:
    def __init__(self, config: AcquisitionConfig | None = None):
        self.config = config or AcquisitionConfig()

    async def resolve(self, doi: str, *, retry_delay: float = 1.0) -> AccessResolution:
        if not self.config.s2_enabled:
            return AccessResolution(identifier_type="doi", identifier_value=doi)

        try:
            url = f"{_S2_API_BASE}/DOI:{doi}?fields={_S2_FIELDS}"
            headers: dict[str, str] = {}
            if self.config.s2_api_key:
                headers["x-api-key"] = self.config.s2_api_key

            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                response = await client.get(url, headers=headers)

            if response.status_code >= 400:
                return AccessResolution(identifier_type="doi", identifier_value=doi)

            try:
                data = json.loads(response.content)
            except Exception:
                return AccessResolution(identifier_type="doi", identifier_value=doi)

            metadata: dict = {}
            if data.get("title"):
                metadata["title"] = data["title"]
            if data.get("abstract"):
                metadata["abstract"] = data["abstract"]
            if data.get("authors"):
                names = [a.get("name", "") for a in data["authors"] if a.get("name")]
                if names:
                    metadata["authors"] = names

            locations: list[AccessLocation] = []
            best_location: AccessLocation | None = None
            oa_pdf = data.get("openAccessPdf")
            if oa_pdf and oa_pdf.get("url"):
                loc = AccessLocation(url=oa_pdf["url"], access_method="oa")
                locations.append(loc)
                best_location = loc

            return AccessResolution(
                identifier_type="doi",
                identifier_value=doi,
                locations=locations,
                best_location=best_location,
                metadata=metadata,
            )

        except Exception as exc:
            logger.warning("SemanticScholarResolver failed for DOI %s: %s", doi, exc)
            return AccessResolution(identifier_type="doi", identifier_value=doi)

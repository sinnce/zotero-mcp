from __future__ import annotations
import json
import logging
from .http_client import fetch_with_retry
from .types import AccessResolution, AccessLocation

logger = logging.getLogger(__name__)
_BASE = "https://api.unpaywall.org/v2"


class UnpaywallClient:
    def __init__(self, email: str = ""):
        self.email = email or "zotero-mcp@users.noreply.github.com"

    async def resolve(self, doi: str, *, retry_delay: float = 1.0) -> AccessResolution:
        url = f"{_BASE}/{doi}?email={self.email}"
        result = await fetch_with_retry(url, retry_delay=retry_delay)
        if not result.success:
            return AccessResolution(identifier_type="doi", identifier_value=doi)

        try:
            data = json.loads(result.content)
        except Exception:
            return AccessResolution(identifier_type="doi", identifier_value=doi)

        locations: list[AccessLocation] = []
        best_raw = data.get("best_oa_location") or {}

        for loc in data.get("oa_locations", []):
            pdf_url = loc.get("url_for_pdf") or loc.get("url")
            if not pdf_url:
                continue
            locations.append(
                AccessLocation(
                    url=pdf_url,
                    access_method="oa",
                    license=loc.get("license"),
                    version=loc.get("version"),
                )
            )

        best_location = None
        if best_raw:
            best_url = best_raw.get("url_for_pdf") or best_raw.get("url")
            if best_url:
                best_location = AccessLocation(
                    url=best_url,
                    access_method="oa",
                    license=best_raw.get("license"),
                    version=best_raw.get("version"),
                )
                if not any(loc.url == best_url for loc in locations):
                    locations.insert(0, best_location)
        elif locations:
            best_location = locations[0]

        metadata = {
            k: v
            for k, v in {
                "title": data.get("title"),
                "year": data.get("year"),
                "journal_name": data.get("journal_name"),
                "publisher": data.get("publisher"),
            }.items()
            if v is not None
        }

        return AccessResolution(
            identifier_type="doi",
            identifier_value=doi,
            locations=locations,
            best_location=best_location,
            metadata=metadata,
        )

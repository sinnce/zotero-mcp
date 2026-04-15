from __future__ import annotations
import logging

import httpx

from .types import AccessResolution, AccessLocation
from .config import AcquisitionConfig

logger = logging.getLogger(__name__)

_NCBI_IDCONV_URL = "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/"
_PMC_OA_URL = "https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi"


class PMCOAResolver:
    def __init__(self, config: AcquisitionConfig | None = None):
        self.config = config or AcquisitionConfig()

    async def resolve(self, doi: str) -> AccessResolution:
        if not self.config.pmc_enabled:
            return AccessResolution(identifier_type="doi", identifier_value=doi)

        try:
            params: dict[str, str] = {"ids": doi, "format": "json"}
            if self.config.ncbi_email:
                params["email"] = self.config.ncbi_email

            async with httpx.AsyncClient(timeout=30.0) as client:
                id_resp = await client.get(_NCBI_IDCONV_URL, params=params)

            if id_resp.status_code != 200:
                return AccessResolution(identifier_type="doi", identifier_value=doi)

            id_data = id_resp.json()
            records = id_data.get("records", [])
            if not records:
                return AccessResolution(identifier_type="doi", identifier_value=doi)

            pmcid = records[0].get("pmcid")
            if not pmcid:
                return AccessResolution(identifier_type="doi", identifier_value=doi)

            async with httpx.AsyncClient(timeout=30.0) as client:
                oa_resp = await client.get(_PMC_OA_URL, params={"id": pmcid, "format": "json"})

            if oa_resp.status_code != 200:
                return AccessResolution(identifier_type="doi", identifier_value=doi)

            oa_data = oa_resp.json()
            oa_records = oa_data.get("records", [])
            if not oa_records:
                return AccessResolution(identifier_type="doi", identifier_value=doi)

            files = oa_records[0].get("files", [])
            pdf_url: str | None = None
            for f in files:
                if f.get("format") == "pdf" and f.get("href"):
                    pdf_url = f["href"]
                    break

            if not pdf_url:
                return AccessResolution(identifier_type="doi", identifier_value=doi)

            loc = AccessLocation(url=pdf_url, access_method="oa")
            return AccessResolution(
                identifier_type="doi",
                identifier_value=doi,
                locations=[loc],
                best_location=loc,
                metadata={},
            )

        except Exception as exc:
            logger.warning("PMCOAResolver failed for DOI %s: %s", doi, exc)
            return AccessResolution(identifier_type="doi", identifier_value=doi)

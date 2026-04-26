"""Orchestration logic for resolve_paper_access tool."""

from __future__ import annotations

import logging

from zotero_mcp.translation_server_client import TranslationServerClient, TranslationServerError

from .arxiv import ArxivResolver
from .config import AcquisitionConfig, load_acquisition_config
from .identifier import NormalizationError, normalize
from .institutional import InstitutionalResolver
from .pmc_oa import PMCOAResolver
from .semantic_scholar import SemanticScholarResolver
from .types import AccessResolution
from .unpaywall import UnpaywallClient
from .url_translators import build_default_url_translator_registry

logger = logging.getLogger(__name__)

_URL_TRANSLATORS = build_default_url_translator_registry()


def _metadata_from_translation_item(item: dict) -> dict:
    metadata: dict = {}
    if item.get("title"):
        metadata["title"] = item["title"]
    if item.get("abstractNote"):
        metadata["abstract"] = item["abstractNote"]
    if item.get("publicationTitle"):
        metadata["journal"] = item["publicationTitle"]
    elif item.get("proceedingsTitle"):
        metadata["journal"] = item["proceedingsTitle"]
    if item.get("date"):
        metadata["year"] = str(item["date"])[:4]
    creators = item.get("creators") or []
    if creators:
        names = []
        for creator in creators:
            first = (creator.get("firstName") or "").strip()
            last = (creator.get("lastName") or "").strip()
            full = " ".join(part for part in [first, last] if part).strip()
            if full:
                names.append(full)
        if names:
            metadata["authors"] = ", ".join(names)
    if item.get("libraryCatalog"):
        metadata["library_catalog"] = item["libraryCatalog"]
    return metadata


def _locations_from_translation_item(item: dict):
    from .types import AccessLocation

    locations = []
    for attachment in item.get("attachments") or []:
        url = attachment.get("url")
        mime_type = (attachment.get("mimeType") or "").lower()
        if not url or mime_type != "application/pdf":
            continue
        locations.append(
            AccessLocation(
                url=url,
                access_method="translation-server",
            )
        )
    return locations


def _translation_server_item_for_url(url: str) -> dict | None:
    client = TranslationServerClient()
    status = client.status()
    if not status.available:
        return None
    try:
        result = client.translate_web(url)
    except TranslationServerError:
        return None
    if not isinstance(result, list) or not result:
        return None
    first = result[0]
    if isinstance(first, dict):
        return first
    return None


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

    source_url = canonical.value if canonical.type == "url" else None
    translated_item = None
    translated_locations = []
    translated_metadata = {}
    if canonical.type == "url":
        translated_item = _translation_server_item_for_url(canonical.value)
        if translated_item:
            translated_locations = _locations_from_translation_item(translated_item)
            translated_metadata = _metadata_from_translation_item(translated_item)
            translated_doi = translated_item.get("DOI")
            if translated_doi:
                try:
                    canonical = normalize(translated_doi)
                except NormalizationError:
                    pass
            else:
                canonical = await _URL_TRANSLATORS.translate(canonical)
        else:
            canonical = await _URL_TRANSLATORS.translate(canonical)
    else:
        canonical = await _URL_TRANSLATORS.translate(canonical)

    locations = []
    best_location = None
    metadata: dict = {"identifier_type": canonical.type, "identifier_value": canonical.value}
    metadata.update(translated_metadata)

    if canonical.type == "doi":
        client = UnpaywallClient(email=config.unpaywall_email)
        result = await client.resolve(canonical.value)
        locations.extend(result.locations)
        best_location = result.best_location
        metadata.update(result.metadata)

        s2 = SemanticScholarResolver(config)
        s2_result = await s2.resolve(canonical.value)
        locations.extend(s2_result.locations)
        if best_location is None and s2_result.best_location:
            best_location = s2_result.best_location
        for k, v in s2_result.metadata.items():
            if k not in metadata:
                metadata[k] = v

        pmc = PMCOAResolver(config)
        pmc_result = await pmc.resolve(canonical.value)
        locations.extend(pmc_result.locations)
        if best_location is None and pmc_result.best_location:
            best_location = pmc_result.best_location

        if source_url:
            from .types import AccessLocation

            locations.extend(translated_locations)
            direct_location = AccessLocation(url=source_url, access_method="direct")
            locations.append(direct_location)
            if best_location is None and translated_locations:
                best_location = translated_locations[0]
            elif best_location is None:
                best_location = direct_location

    elif canonical.type == "arxiv":
        resolver = ArxivResolver()
        result = await resolver.resolve(canonical.value)
        locations.extend(result.locations)
        best_location = result.best_location
        metadata.update(result.metadata)

    elif canonical.type == "url":
        from .types import AccessLocation

        locations.append(AccessLocation(url=canonical.value, access_method="direct"))
        locations.extend(translated_locations)
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

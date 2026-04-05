"""Orchestration logic for resolve_paper_access tool."""

from __future__ import annotations
import asyncio
from .identifier import normalize, NormalizationError
from .unpaywall import UnpaywallClient
from .arxiv import ArxivResolver
from .institutional import InstitutionalResolver
from .config import AcquisitionConfig, load_acquisition_config
from .types import AccessResolution


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

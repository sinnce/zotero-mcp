"""Ingest orchestration — create Zotero item + optional attachment."""

from __future__ import annotations
import logging
from pathlib import Path
from .types import IngestResult, PipelineError, ProvenanceMetadata

logger = logging.getLogger(__name__)


def _check_duplicate(zot, doi: str | None, arxiv_id: str | None, title: str) -> str | None:
    if doi:
        try:
            results = zot.items(q=doi, limit=5)
            for item in results:
                data = item.get("data", {})
                if data.get("DOI", "").lower() == doi.lower():
                    return data.get("key")
        except Exception:
            pass
    if arxiv_id:
        try:
            results = zot.items(q=arxiv_id, limit=5)
            for item in results:
                data = item.get("data", {})
                extra = data.get("extra", "")
                if arxiv_id.lower() in extra.lower():
                    return data.get("key")
        except Exception:
            pass
    return None


def ingest_paper(
    write_zot,
    read_zot,
    *,
    title: str,
    doi: str | None = None,
    arxiv_id: str | None = None,
    authors: str | None = None,
    journal: str | None = None,
    year: str | None = None,
    abstract: str | None = None,
    file_path: Path | None = None,
    provenance: ProvenanceMetadata | None = None,
) -> IngestResult | PipelineError:
    if provenance is None:
        provenance = ProvenanceMetadata()

    existing_key = _check_duplicate(read_zot, doi, arxiv_id, title)
    if existing_key:
        return IngestResult(
            item_key=existing_key,
            provenance=ProvenanceMetadata(access_source="duplicate_detected"),
        )

    try:
        template = write_zot.item_template("journalArticle")
    except Exception as e:
        return PipelineError(step="ingest", code="TEMPLATE_ERROR", message=str(e), recoverable=False)

    template["title"] = title
    if doi:
        template["DOI"] = doi
    if journal:
        template["publicationTitle"] = journal
    if year:
        template["date"] = year
    if abstract:
        template["abstractNote"] = abstract
    if authors:
        template["creators"] = [{"creatorType": "author", "name": a.strip()} for a in authors.split(",") if a.strip()]

    prov_lines = ["[Paper Ingest Provenance]"]
    for field in [
        "access_source",
        "resolver_name",
        "proxy_provider",
        "artifact_type",
        "extraction_backend",
        "fallback_reason",
        "license",
        "has_fulltext",
    ]:
        val = getattr(provenance, field, None)
        if val is not None:
            prov_lines.append(f"{field}: {val}")
    template["extra"] = "\n".join(prov_lines)

    try:
        result = write_zot.create_items([template])
        created = result.get("successful", {})
        if not created:
            failed = result.get("failed", {})
            msg = str(next(iter(failed.values()), "unknown error")) if failed else "No items created"
            return PipelineError(step="ingest", code="CREATE_FAILED", message=msg, recoverable=True)
        item_key = list(created.values())[0].get("key") or list(created.keys())[0]
    except Exception as e:
        return PipelineError(step="ingest", code="CREATE_ERROR", message=str(e), recoverable=True)

    attachment_key = None
    if file_path and Path(file_path).exists():
        try:
            write_zot.attachment_both(
                [(str(Path(file_path).name), str(file_path))],
                parentid=item_key,
            )
            children = write_zot.children(item_key)
            for child in children or []:
                if child.get("data", {}).get("itemType") == "attachment":
                    attachment_key = child["data"]["key"]
                    break
        except Exception as e:
            logger.warning(f"Attachment failed for {item_key}: {e}")

    return IngestResult(item_key=item_key, attachment_key=attachment_key, provenance=provenance)

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from fastmcp import Context

from zotero_mcp._app import mcp
from zotero_mcp.acquisition.async_bridge_client import AsyncBridgeClient as BridgeClient
from zotero_mcp.acquisition.bridge_client import (
    ARTIFACT_SNAPSHOT_FAILED,
    BRIDGE_REQUEST_INVALID,
    BridgeDownloadRequest,
    snapshot_bridge_artifact,
)
from zotero_mcp.acquisition.config import load_acquisition_config
from zotero_mcp.acquisition.download import ArtifactDownloader
from zotero_mcp.acquisition.ingest import ingest_paper
from zotero_mcp.acquisition.resolver import resolve_access
from zotero_mcp.acquisition.types import PipelineError, ProvenanceMetadata
from zotero_mcp.tools._helpers import _get_write_client

logger = logging.getLogger(__name__)

_EXPLICIT_PAYWALL_CODES = {"AUTH_REQUIRED", "HTML_LANDING", "ACCESS_DENIED", "DOMAIN_BLOCKED"}
# UNAUTHORIZED means the bridge rejected our bearer token; no other route or
# retry can succeed with it. BRIDGE_REQUEST_INVALID is a client-side rejection
# of caller input that the bridge contract would refuse.
_TERMINAL_BRIDGE_CODES = {
    "AUTH_REQUIRED",
    "DOMAIN_BLOCKED",
    "CAPTCHA",
    "CANCELLED",
    "UNAUTHORIZED",
    BRIDGE_REQUEST_INVALID,
}


def _run_auto_ingest(ctx, resolution, file_path, identifier):
    meta = resolution.metadata or {}
    title = meta.get("title")
    if not title:
        logger.warning("auto_ingest: skipping — no title in metadata for %s", identifier)
        return None

    prov = ProvenanceMetadata(
        access_source=resolution.best_location.access_method if resolution.best_location else None,
    )

    doi = resolution.identifier_value if resolution.identifier_type == "doi" else None

    try:
        read_zot, write_zot = _get_write_client(ctx)
    except Exception as exc:
        logger.warning("auto_ingest: skipping — cannot get write client: %s", exc)
        return None

    result = ingest_paper(
        write_zot=write_zot,
        read_zot=read_zot,
        title=title,
        doi=doi,
        authors=meta.get("authors"),
        journal=meta.get("journal"),
        year=meta.get("year"),
        abstract=meta.get("abstract"),
        file_path=Path(file_path) if file_path else None,
        provenance=prov,
    )

    if isinstance(result, PipelineError):
        logger.warning("auto_ingest: ingest failed [%s]: %s", result.code, result.message)
        return None

    return result.item_key


def _bridge_doi(identifier: str, resolution) -> str:
    return resolution.identifier_value if resolution.identifier_type == "doi" else identifier


def _is_explicit_paywall(result) -> bool:
    return result.status == "auth_required" or result.error_code in _EXPLICIT_PAYWALL_CODES


def _is_terminal_bridge_result(result) -> bool:
    return result.status == "auth_required" or result.error_code in _TERMINAL_BRIDGE_CODES


def _should_try_direct_before_libproxy(config, resolution, location, session_name: str | None) -> bool:
    inst = config.institutional_access
    return (
        bool(session_name)
        and resolution.identifier_type == "doi"
        and location.requires_session
        and location.session_kind == "libproxy"
        and inst.enabled
        and inst.provider == "libproxy"
        and _is_explicit_libproxy_url(config, location)
    )


def _is_explicit_libproxy_url(config, location) -> bool:
    inst = config.institutional_access
    if not inst.enabled or inst.provider != "libproxy" or not inst.libproxy_base_url:
        return False
    try:
        candidate = urlparse(location.url)
        libproxy = urlparse(inst.libproxy_base_url)
    except Exception:
        return False
    return bool(candidate.netloc and candidate.netloc == libproxy.netloc)


def _bridge_artifact_failure(error_code: str) -> dict:
    logger.warning("bridge artifact rejected [%s]", error_code)
    return {
        "status": "failed",
        "error_code": error_code,
        "message": f"[{error_code}] Bridge artifact failed integrity verification",
    }


def _bridge_unauthorized_failure() -> dict:
    return {
        "status": "failed",
        "error_code": "UNAUTHORIZED",
        "message": "[UNAUTHORIZED] Bridge rejected the configured authentication",
    }


async def _complete_bridge_download(
    ctx, resolution, identifier: str, result, session_name: str, access_source: str, message: str, auto_ingest: bool
) -> dict:
    # Fail closed before reporting or ingesting a bridge artifact whose staged
    # bytes do not match the bridge-declared sha256 and size_bytes.
    if not auto_ingest:
        # The caller gets a verified copy it owns, not the bridge-owned staged
        # path, which the bridge may replace or expire after verification.
        artifact_dir = tempfile.mkdtemp(prefix="zotero-mcp-bridge-artifact-")
        error_code, snapshot_path = await asyncio.to_thread(snapshot_bridge_artifact, result, artifact_dir)
        if error_code is not None or snapshot_path is None:
            shutil.rmtree(artifact_dir, ignore_errors=True)
            return _bridge_artifact_failure(error_code or ARTIFACT_SNAPSHOT_FAILED)
        return _build_bridge_output(result, session_name, access_source, message, file_path=str(snapshot_path))

    # The staged path stays writable by the bridge, so ingest reads a private
    # snapshot whose bytes are exactly the ones that were hashed.
    snapshot_dir = tempfile.mkdtemp(prefix="zotero-mcp-bridge-verified-")
    try:
        error_code, snapshot_path = await asyncio.to_thread(snapshot_bridge_artifact, result, snapshot_dir)
        if error_code is not None:
            return _bridge_artifact_failure(error_code)
        out = _build_bridge_output(result, session_name, access_source, message)
        item_key = _run_auto_ingest(ctx, resolution, snapshot_path, identifier)
        if item_key:
            out["zotero_item_key"] = item_key
        return out
    finally:
        shutil.rmtree(snapshot_dir, ignore_errors=True)


def _build_bridge_output(
    result, session_name: str, access_source: str, message: str, file_path: str | None = None
) -> dict:
    return {
        "status": "complete",
        "file_path": file_path if file_path is not None else result.file_path,
        "provenance": {
            "bridge_session": session_name,
            "access_source": access_source,
        },
        "message": message,
    }


@mcp.tool()
async def acquire_paper(
    identifier: str,
    session_name: str | None = None,
    auto_ingest: bool | None = None,
    ctx: Context | None = None,
) -> dict:
    config = load_acquisition_config()
    effective_auto_ingest = config.auto_ingest if auto_ingest is None else auto_ingest
    resolution = await resolve_access(identifier, config)

    if not resolution or not resolution.best_location:
        return {"status": "failed", "message": f"Could not resolve: {identifier}"}

    location = resolution.best_location

    if session_name and (location.requires_session or _is_explicit_libproxy_url(config, location)):
        bridge = BridgeClient()
        health = await bridge.health()
        if health.error_code == "UNAUTHORIZED":
            return _bridge_unauthorized_failure()
        if health.available:
            bridge_identifier = _bridge_doi(identifier, resolution)
            result = None

            if _should_try_direct_before_libproxy(config, resolution, location, session_name):
                direct_result = await bridge.download(
                    BridgeDownloadRequest(
                        doi=bridge_identifier,
                        candidate_url=f"https://doi.org/{resolution.identifier_value}",
                        session_name=session_name,
                    )
                )
                if direct_result.status == "complete" and direct_result.file_path:
                    return await _complete_bridge_download(
                        ctx,
                        resolution,
                        identifier,
                        direct_result,
                        session_name,
                        access_source="direct",
                        message="Downloaded via direct browser access",
                        auto_ingest=effective_auto_ingest,
                    )

                if _is_explicit_paywall(direct_result):
                    result = await bridge.download(
                        BridgeDownloadRequest(
                            doi=bridge_identifier,
                            candidate_url=location.url,
                            session_name=session_name,
                        )
                    )
                else:
                    result = direct_result
            else:
                result = await bridge.download(
                    BridgeDownloadRequest(
                        doi=bridge_identifier,
                        candidate_url=location.url,
                        session_name=session_name,
                    )
                )

            if result and result.status == "complete" and result.file_path:
                return await _complete_bridge_download(
                    ctx,
                    resolution,
                    identifier,
                    result,
                    session_name,
                    access_source="institutional",
                    message=f"Downloaded via bridge session '{session_name}'",
                    auto_ingest=effective_auto_ingest,
                )
            if result and _is_terminal_bridge_result(result):
                return {
                    "status": "failed",
                    "message": f"[{result.error_code or 'BRIDGE_UNAVAILABLE'}] Bridge download failed",
                }

    downloader = ArtifactDownloader(config)
    dest_dir = Path(tempfile.mkdtemp(prefix="zotero-mcp-acquire-"))
    download_result = await downloader.download(location.url, dest_dir=dest_dir)

    if isinstance(download_result, PipelineError):
        return {"status": "failed", "message": f"[{download_result.code}] {download_result.message}"}

    out = {
        "status": "complete",
        "file_path": str(download_result.file_path),
        "provenance": {"access_source": location.access_method or "url"},
        "message": "Downloaded via HTTP",
    }

    if effective_auto_ingest:
        item_key = _run_auto_ingest(ctx, resolution, download_result.file_path, identifier)
        if item_key:
            out["zotero_item_key"] = item_key

    return out

import dataclasses
import hashlib
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zotero_mcp.acquisition.bridge_client import (
    ARTIFACT_HASH_MISMATCH,
    ARTIFACT_MISSING,
    ARTIFACT_NOT_REGULAR,
    ARTIFACT_SIZE_MISMATCH,
    ARTIFACT_UNVERIFIABLE,
    verify_bridge_artifact,
)
from zotero_mcp.acquisition.config import AcquisitionConfig, InstitutionalConfig
from zotero_mcp.acquisition.types import AccessLocation, AccessResolution, IngestResult
from zotero_mcp.tools.acquire_paper import acquire_paper

CONTENT = b"%PDF-1.7 bridge integrity fixture\n"


def _tamper_hash(result):
    return dataclasses.replace(result, sha256=hashlib.sha256(b"other bytes").hexdigest())


def _tamper_size(result):
    return dataclasses.replace(result, size_bytes=result.size_bytes + 1)


def _remove_file(result):
    os.unlink(result.file_path)
    return result


def _symlink(result):
    # The link target has exactly the declared bytes; only the link itself is rejected.
    link = f"{result.file_path}.link"
    os.symlink(result.file_path, link)
    return dataclasses.replace(result, file_path=link)


def _drop_hash(result):
    return dataclasses.replace(result, sha256=None)


FAILURES = [
    pytest.param(_tamper_hash, ARTIFACT_HASH_MISMATCH, id="hash-mismatch"),
    pytest.param(_tamper_size, ARTIFACT_SIZE_MISMATCH, id="size-mismatch"),
    pytest.param(_remove_file, ARTIFACT_MISSING, id="missing-file"),
    pytest.param(_symlink, ARTIFACT_NOT_REGULAR, id="symlink"),
    pytest.param(_drop_hash, ARTIFACT_UNVERIFIABLE, id="undeclared-hash"),
]


def test_verify_accepts_matching_regular_file(bridge_artifact):
    assert verify_bridge_artifact(bridge_artifact(content=CONTENT)) is None


@pytest.mark.parametrize(("mutate", "error_code"), FAILURES)
def test_verify_rejects_integrity_failures(bridge_artifact, mutate, error_code):
    assert verify_bridge_artifact(mutate(bridge_artifact(content=CONTENT))) == error_code


def test_verify_rejects_same_size_content_substitution(bridge_artifact):
    result = bridge_artifact(content=CONTENT)
    with open(result.file_path, "r+b") as handle:
        handle.write(b"X")

    assert verify_bridge_artifact(result) == ARTIFACT_HASH_MISMATCH


def test_verify_rejects_directory_and_fifo(bridge_artifact, tmp_path):
    result = bridge_artifact(content=CONTENT)
    directory = tmp_path / "staged-dir"
    directory.mkdir()
    fifo = tmp_path / "staged-fifo"
    os.mkfifo(fifo)

    assert verify_bridge_artifact(dataclasses.replace(result, file_path=str(directory))) == ARTIFACT_NOT_REGULAR
    assert verify_bridge_artifact(dataclasses.replace(result, file_path=str(fifo))) == ARTIFACT_NOT_REGULAR


def _institutional_resolution() -> AccessResolution:
    location = AccessLocation(
        url="https://publisher.example/paper.pdf",
        access_method="institutional",
        requires_session=True,
    )
    return AccessResolution(
        identifier_type="doi",
        identifier_value="10.1000/example",
        locations=[location],
        best_location=location,
        metadata={"title": "Integrity Paper"},
    )


def _direct_resolution() -> AccessResolution:
    location = AccessLocation(
        url="https://libproxy.snu.ac.kr/link.n2s?url=https%3A%2F%2Fdoi.org%2F10.1000%2Fexample",
        access_method="institutional",
        requires_session=True,
        session_kind="libproxy",
    )
    return AccessResolution(
        identifier_type="doi",
        identifier_value="10.1000/example",
        locations=[location],
        best_location=location,
        metadata={"title": "Integrity Paper"},
    )


_LIBPROXY_CONFIG = AcquisitionConfig(
    institutional_access=InstitutionalConfig(
        enabled=True,
        provider="libproxy",
        libproxy_base_url="https://libproxy.snu.ac.kr/link.n2s",
    )
)

ROUTES = [
    pytest.param(_institutional_resolution, AcquisitionConfig(), id="institutional"),
    pytest.param(_direct_resolution, _LIBPROXY_CONFIG, id="direct"),
]


async def _acquire(resolution, config, bridge_result):
    zot = MagicMock()
    with (
        patch("zotero_mcp.tools.acquire_paper.load_acquisition_config", return_value=config),
        patch("zotero_mcp.tools.acquire_paper.resolve_access", new=AsyncMock(return_value=resolution)),
        patch("zotero_mcp.tools.acquire_paper.BridgeClient") as bridge_class,
        patch("zotero_mcp.tools.acquire_paper.ArtifactDownloader") as downloader_class,
        patch("zotero_mcp.tools.acquire_paper._get_write_client", return_value=(zot, zot)),
        patch("zotero_mcp.tools.acquire_paper.ingest_paper", return_value=IngestResult(item_key="INGESTED")) as ingest,
    ):
        bridge = MagicMock()
        bridge.is_available = AsyncMock(return_value=True)
        bridge.download = AsyncMock(return_value=bridge_result)
        bridge_class.return_value = bridge
        result = await acquire_paper("10.1000/example", session_name="campus", auto_ingest=True)
    return result, ingest, downloader_class, bridge


@pytest.mark.asyncio
@pytest.mark.parametrize(("make_resolution", "config"), ROUTES)
async def test_acquire_ingests_verified_bridge_artifact(bridge_artifact, make_resolution, config):
    bridge_result = bridge_artifact(content=CONTENT)

    result, ingest, downloader_class, _ = await _acquire(make_resolution(), config, bridge_result)

    assert result["status"] == "complete"
    assert result["zotero_item_key"] == "INGESTED"
    ingest.assert_called_once()
    assert str(ingest.call_args.kwargs["file_path"]) == bridge_result.file_path
    downloader_class.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(("make_resolution", "config"), ROUTES)
@pytest.mark.parametrize(("mutate", "error_code"), FAILURES)
async def test_acquire_fails_closed_without_ingest_on_integrity_failure(
    bridge_artifact, make_resolution, config, mutate, error_code
):
    bridge_result = mutate(bridge_artifact(content=CONTENT))

    result, ingest, downloader_class, bridge = await _acquire(make_resolution(), config, bridge_result)

    assert result["status"] == "failed"
    assert result["error_code"] == error_code
    assert f"[{error_code}]" in result["message"]
    assert "file_path" not in result
    assert "zotero_item_key" not in result
    ingest.assert_not_called()
    downloader_class.assert_not_called()
    bridge.download.assert_awaited_once()

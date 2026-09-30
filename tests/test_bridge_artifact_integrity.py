import dataclasses
import errno
import hashlib
import os
import stat
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zotero_mcp.acquisition.bridge_client import (
    ARTIFACT_HASH_MISMATCH,
    ARTIFACT_MISSING,
    ARTIFACT_NOT_REGULAR,
    ARTIFACT_SIZE_MISMATCH,
    ARTIFACT_SNAPSHOT_FAILED,
    ARTIFACT_UNREADABLE,
    ARTIFACT_UNVERIFIABLE,
    BridgeHealthResult,
    snapshot_bridge_artifact,
    verify_bridge_artifact,
)
from zotero_mcp.acquisition.config import AcquisitionConfig, InstitutionalConfig
from zotero_mcp.acquisition.types import AccessLocation, AccessResolution, IngestResult
from zotero_mcp.tools.acquire_paper import acquire_paper

CONTENT = b"%PDF-1.7 bridge integrity fixture\n"
READY_HEALTH = BridgeHealthResult(True, "http://127.0.0.1:9870", "ready", "Bridge is ready.")


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


def test_snapshot_copies_exactly_the_verified_bytes(bridge_artifact, tmp_path):
    result = bridge_artifact(content=CONTENT)
    dest = tmp_path / "private"
    dest.mkdir()

    error_code, snapshot = snapshot_bridge_artifact(result, dest)

    assert error_code is None
    assert snapshot == dest / "paper.pdf"
    assert snapshot.read_bytes() == CONTENT
    assert not snapshot.is_symlink()
    assert os.stat(snapshot).st_ino != os.stat(result.file_path).st_ino
    assert os.stat(snapshot).st_mode & 0o777 == 0o600


@pytest.mark.parametrize(("mutate", "error_code"), FAILURES)
def test_snapshot_rejects_integrity_failures_without_leaving_bytes(bridge_artifact, tmp_path, mutate, error_code):
    result = mutate(bridge_artifact(content=CONTENT))
    dest = tmp_path / "private"
    dest.mkdir()

    assert snapshot_bridge_artifact(result, dest) == (error_code, None)
    assert list(dest.iterdir()) == []


def test_snapshot_discards_partial_copy_on_same_size_substitution(bridge_artifact, tmp_path):
    result = bridge_artifact(content=CONTENT)
    with open(result.file_path, "r+b") as handle:
        handle.write(b"X")
    dest = tmp_path / "private"
    dest.mkdir()

    assert snapshot_bridge_artifact(result, dest) == (ARTIFACT_HASH_MISMATCH, None)
    assert list(dest.iterdir()) == []


def test_snapshot_refuses_to_follow_or_overwrite_existing_entry(bridge_artifact, tmp_path):
    result = bridge_artifact(content=CONTENT)
    dest = tmp_path / "private"
    dest.mkdir()
    victim = tmp_path / "victim"
    victim.write_bytes(b"keep")
    (dest / "paper.pdf").symlink_to(victim)

    error_code, snapshot = snapshot_bridge_artifact(result, dest)

    assert error_code is not None
    assert snapshot is None
    assert victim.read_bytes() == b"keep"


def _fail_open_of(path, error_number):
    real_open = os.open

    def _open(file, flags, *args, **kwargs):
        if os.fspath(file) == path:
            raise OSError(error_number, os.strerror(error_number), path)
        return real_open(file, flags, *args, **kwargs)

    return _open


@pytest.mark.parametrize(
    ("error_number", "error_code"),
    [
        pytest.param(errno.EACCES, ARTIFACT_UNREADABLE, id="eacces"),
        pytest.param(errno.EPERM, ARTIFACT_UNREADABLE, id="eperm"),
        pytest.param(errno.EIO, ARTIFACT_UNREADABLE, id="eio"),
        pytest.param(errno.EMFILE, ARTIFACT_UNREADABLE, id="emfile"),
        # A symlink swapped in after lstat, or a socket node, is non-regular.
        pytest.param(errno.ELOOP, ARTIFACT_NOT_REGULAR, id="eloop"),
        pytest.param(errno.ENXIO, ARTIFACT_NOT_REGULAR, id="enxio"),
    ],
)
def test_open_failures_are_classified_by_cause(bridge_artifact, tmp_path, monkeypatch, error_number, error_code):
    result = bridge_artifact(content=CONTENT)
    dest = tmp_path / "private"
    dest.mkdir()
    monkeypatch.setattr(os, "open", _fail_open_of(result.file_path, error_number))

    assert verify_bridge_artifact(result) == error_code
    assert snapshot_bridge_artifact(result, dest) == (error_code, None)
    assert list(dest.iterdir()) == []


def test_real_unreadable_regular_file_is_not_classified_as_non_regular(bridge_artifact):
    result = bridge_artifact(content=CONTENT)
    os.chmod(result.file_path, 0)
    try:
        error_code = verify_bridge_artifact(result)
    finally:
        os.chmod(result.file_path, stat.S_IRUSR | stat.S_IWUSR)

    # A privileged runner can still read the file; nobody may call it non-regular.
    assert error_code in ({None} if os.geteuid() == 0 else {ARTIFACT_UNREADABLE})


def test_snapshot_destination_failure_has_its_own_code(bridge_artifact, tmp_path):
    result = bridge_artifact(content=CONTENT)
    dest = tmp_path / "private"
    dest.mkdir()
    victim = tmp_path / "victim"
    victim.write_bytes(b"keep")
    (dest / "paper.pdf").symlink_to(victim)

    assert snapshot_bridge_artifact(result, dest) == (ARTIFACT_SNAPSHOT_FAILED, None)
    assert victim.read_bytes() == b"keep"
    assert (dest / "paper.pdf").is_symlink()


def test_snapshot_into_missing_destination_is_not_an_artifact_read_failure(bridge_artifact, tmp_path):
    result = bridge_artifact(content=CONTENT)

    assert snapshot_bridge_artifact(result, tmp_path / "absent") == (ARTIFACT_SNAPSHOT_FAILED, None)


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


async def _acquire(resolution, config, bridge_result, auto_ingest=True, ingest_side_effect=None):
    zot = MagicMock()
    with (
        patch("zotero_mcp.tools.acquire_paper.load_acquisition_config", return_value=config),
        patch("zotero_mcp.tools.acquire_paper.resolve_access", new=AsyncMock(return_value=resolution)),
        patch("zotero_mcp.tools.acquire_paper.BridgeClient") as bridge_class,
        patch("zotero_mcp.tools.acquire_paper.ArtifactDownloader") as downloader_class,
        patch("zotero_mcp.tools.acquire_paper._get_write_client", return_value=(zot, zot)),
        patch(
            "zotero_mcp.tools.acquire_paper.ingest_paper",
            return_value=IngestResult(item_key="INGESTED"),
            side_effect=ingest_side_effect,
        ) as ingest,
    ):
        bridge = MagicMock()
        bridge.health = AsyncMock(return_value=READY_HEALTH)
        bridge.download = AsyncMock(return_value=bridge_result)
        bridge_class.return_value = bridge
        result = await acquire_paper("10.1000/example", session_name="campus", auto_ingest=auto_ingest)
    return result, ingest, downloader_class, bridge


@pytest.mark.asyncio
@pytest.mark.parametrize(("make_resolution", "config"), ROUTES)
async def test_acquire_ingests_verified_bridge_artifact(bridge_artifact, make_resolution, config):
    bridge_result = bridge_artifact(content=CONTENT)

    ingested = {}

    def _ingest(**kwargs):
        ingested["path"] = kwargs["file_path"]
        ingested["bytes"] = kwargs["file_path"].read_bytes()
        return IngestResult(item_key="INGESTED")

    result, ingest, downloader_class, _ = await _acquire(
        make_resolution(), config, bridge_result, ingest_side_effect=_ingest
    )

    assert result["status"] == "complete"
    assert result["file_path"] == bridge_result.file_path
    assert result["zotero_item_key"] == "INGESTED"
    ingest.assert_called_once()
    assert ingested["bytes"] == CONTENT
    # Ingest reads a private verified snapshot that keeps the staged basename
    # and is removed once ingest returns.
    assert ingested["path"].name == "paper.pdf"
    assert str(ingested["path"]) != bridge_result.file_path
    assert not ingested["path"].exists()
    downloader_class.assert_not_called()


def _rewrite_in_place(path):
    with open(path, "r+b") as handle:
        handle.write(b"X")


def _replace_by_rename(path):
    replacement = f"{path}.evil"
    with open(replacement, "wb") as handle:
        handle.write(b"%PDF-1.7 attacker-controlled bytes!\n")
    os.replace(replacement, path)


def _replace_by_symlink(path):
    decoy = f"{path}.decoy"
    with open(decoy, "wb") as handle:
        handle.write(b"%PDF-1.7 attacker-controlled bytes!\n")
    os.unlink(path)
    os.symlink(decoy, path)


@pytest.mark.asyncio
@pytest.mark.parametrize(("make_resolution", "config"), ROUTES)
@pytest.mark.parametrize(
    "replace",
    [
        pytest.param(_rewrite_in_place, id="rewrite-in-place"),
        pytest.param(_replace_by_rename, id="rename-over"),
        pytest.param(_replace_by_symlink, id="symlink-swap"),
    ],
)
async def test_acquire_ingests_verified_bytes_despite_post_verification_replacement(
    bridge_artifact, make_resolution, config, replace
):
    bridge_result = bridge_artifact(content=CONTENT)
    ingested = {}

    def _ingest(**kwargs):
        # The bridge-owned staged file is swapped after verification and
        # before ingest reads its input.
        replace(bridge_result.file_path)
        assert Path(bridge_result.file_path).read_bytes() != CONTENT
        ingested["bytes"] = kwargs["file_path"].read_bytes()
        return IngestResult(item_key="INGESTED")

    result, ingest, _, _ = await _acquire(make_resolution(), config, bridge_result, ingest_side_effect=_ingest)

    assert result["status"] == "complete"
    ingest.assert_called_once()
    assert ingested["bytes"] == CONTENT


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


@pytest.mark.asyncio
@pytest.mark.parametrize(("make_resolution", "config"), ROUTES)
@pytest.mark.parametrize(("mutate", "error_code"), FAILURES)
async def test_acquire_fails_closed_without_auto_ingest_on_integrity_failure(
    bridge_artifact, make_resolution, config, mutate, error_code
):
    bridge_result = mutate(bridge_artifact(content=CONTENT))

    result, ingest, downloader_class, _ = await _acquire(make_resolution(), config, bridge_result, auto_ingest=False)

    assert result["status"] == "failed"
    assert result["error_code"] == error_code
    assert "file_path" not in result
    ingest.assert_not_called()
    downloader_class.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(("make_resolution", "config"), ROUTES)
async def test_acquire_leaves_no_snapshot_after_rejected_artifact(
    bridge_artifact, make_resolution, config, tmp_path, monkeypatch
):
    snapshot_root = tmp_path / "snapshots"
    snapshot_root.mkdir()
    monkeypatch.setattr("tempfile.tempdir", str(snapshot_root))
    bridge_result = _tamper_hash(bridge_artifact(content=CONTENT))

    result, ingest, _, _ = await _acquire(make_resolution(), config, bridge_result)

    assert result["error_code"] == ARTIFACT_HASH_MISMATCH
    ingest.assert_not_called()
    assert list(snapshot_root.iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("make_resolution", "config"), ROUTES)
async def test_acquire_classifies_unreadable_bridge_artifact(bridge_artifact, make_resolution, config, monkeypatch):
    bridge_result = bridge_artifact(content=CONTENT)
    monkeypatch.setattr(os, "open", _fail_open_of(bridge_result.file_path, errno.EACCES))

    result, ingest, downloader_class, _ = await _acquire(make_resolution(), config, bridge_result)

    assert result["status"] == "failed"
    assert result["error_code"] == ARTIFACT_UNREADABLE
    ingest.assert_not_called()
    downloader_class.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(("make_resolution", "config"), ROUTES)
@pytest.mark.parametrize(
    "replace",
    [
        pytest.param(_rewrite_in_place, id="rewrite-in-place"),
        pytest.param(_replace_by_rename, id="rename-over"),
        pytest.param(_replace_by_symlink, id="symlink-swap"),
        pytest.param(os.unlink, id="expired"),
    ],
)
async def test_acquire_without_ingest_returns_caller_owned_verified_copy(
    bridge_artifact, make_resolution, config, tmp_path, monkeypatch, replace
):
    caller_root = tmp_path / "caller"
    caller_root.mkdir()
    monkeypatch.setattr("tempfile.tempdir", str(caller_root))
    bridge_result = bridge_artifact(content=CONTENT)

    result, ingest, downloader_class, _ = await _acquire(make_resolution(), config, bridge_result, auto_ingest=False)

    assert result["status"] == "complete"
    returned = Path(result["file_path"])
    assert str(returned) != bridge_result.file_path
    assert returned.is_relative_to(caller_root)
    assert returned.name == "paper.pdf"
    assert not returned.is_symlink()
    assert os.stat(returned).st_mode & 0o777 == 0o600
    assert os.stat(returned.parent).st_mode & 0o777 == 0o700
    # The bridge may replace or expire its staged file afterwards; the
    # caller's copy keeps exactly the verified bytes.
    replace(bridge_result.file_path)
    assert returned.read_bytes() == CONTENT
    ingest.assert_not_called()
    downloader_class.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(("make_resolution", "config"), ROUTES)
async def test_acquire_without_ingest_leaves_no_copy_after_rejected_artifact(
    bridge_artifact, make_resolution, config, tmp_path, monkeypatch
):
    caller_root = tmp_path / "caller"
    caller_root.mkdir()
    monkeypatch.setattr("tempfile.tempdir", str(caller_root))
    bridge_result = _tamper_hash(bridge_artifact(content=CONTENT))

    result, ingest, _, _ = await _acquire(make_resolution(), config, bridge_result, auto_ingest=False)

    assert result["error_code"] == ARTIFACT_HASH_MISMATCH
    ingest.assert_not_called()
    assert list(caller_root.iterdir()) == []

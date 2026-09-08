"""Isolated, provenance-bound construction of a sealed Zotero Chroma index."""

from __future__ import annotations

import ctypes
import hashlib
import importlib.metadata
import json
import math
import multiprocessing
import os
import re
import struct
import sys
import tempfile
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, cast, runtime_checkable
from urllib.parse import urlsplit

from zotero_mcp._version import __version__
from zotero_mcp.chroma_client import (
    EMBEDDING_DESCRIPTOR_SCHEMA_VERSION,
    ISOLATED_3072_MODELS,
    ResolvedEmbedding,
    create_pinned_embedding_function,
)
from zotero_mcp.utils import format_creators

BUILD_SCHEMA_VERSION = "zotero-mcp-index-build/1.0.0"
PUBLICATION_SCHEMA_VERSION = "zotero-mcp-index-publication/1.0.0"
TEXT_POLICY_VERSION = "zotero-mcp-metadata-text/1.0.0"
COLLECTION_METADATA_SCHEMA = "zotero-mcp-index-metadata/1.0.0"
EXCLUDED_ITEM_TYPES = frozenset({"attachment", "note", "annotation"})
TEXT_VECTOR_DIMENSION = 3072
DEFAULT_COLLECTION = "zotero_library"
DEFAULT_PAGE_SIZE = 100


class IsolatedIndexBuildError(RuntimeError):
    """The isolated index could not be proven complete and immutable."""

    code = "isolated_index_build_failed"


@runtime_checkable
class _ArrayLike(Protocol):
    def tolist(self) -> object: ...


def source_identity(client: Any) -> dict[str, object]:
    """Return the stable, credential-free Zotero source identity."""

    endpoint = getattr(client, "endpoint", None)
    library_id = getattr(client, "library_id", None)
    library_type = getattr(client, "library_type", None)
    if not isinstance(endpoint, str) or not endpoint.strip():
        raise IsolatedIndexBuildError("Zotero API endpoint is unknown")
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise IsolatedIndexBuildError("Zotero API endpoint must be absolute HTTP(S)")
    if library_id is None or not str(library_id).strip() or library_type is None or not str(library_type).strip():
        raise IsolatedIndexBuildError("Zotero library identity is unknown")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return {
        "source_kind": "local-api" if bool(getattr(client, "local", False)) else "web-api",
        "endpoint": {
            "scheme": parsed.scheme.lower(),
            "host": parsed.hostname.lower(),
            "port": port,
            "path": parsed.path or "/",
        },
        "library_type": str(library_type),
        "library_id": str(library_id),
        "acquisition_policy": "top-level-items-exact-pagination",
        "text_policy": TEXT_POLICY_VERSION,
        "exclusion_policy": sorted(EXCLUDED_ITEM_TYPES),
        "deduplication_policy": "item-key-only-no-semantic-deduplication",
    }


def metadata_document(item: Mapping[str, Any]) -> str:
    """Create the exact frozen metadata-only document used by the legacy indexer."""

    raw_data = item.get("data")
    if not isinstance(raw_data, Mapping):
        raise IsolatedIndexBuildError("Zotero item data is not an object")
    data: dict[str, Any] = dict(raw_data)
    parts: list[str] = []
    raw_creators = data.get("creators", [])
    creators = raw_creators if isinstance(raw_creators, list) else []
    for value in (
        data.get("title", ""),
        format_creators(creators),
        data.get("abstractNote", ""),
        data.get("publicationTitle", ""),
    ):
        if value:
            parts.append(str(value))
    tags = data.get("tags")
    if isinstance(tags, list):
        tag_text = " ".join(str(tag.get("tag", "")) for tag in tags if isinstance(tag, Mapping))
        if tag_text:
            parts.append(tag_text)
    note = data.get("note")
    if note:
        note_text = re.sub(r"<[^>]+>", "", str(note))
        if note_text:
            parts.append(note_text)
    return " ".join(parts)


def build_isolated_index(
    zotero_client: Any,
    resolved: ResolvedEmbedding,
    output_path: str | Path,
    *,
    collection_name: str = DEFAULT_COLLECTION,
    protected_paths: Sequence[str | Path] = (),
    embedding_factory: Callable[[ResolvedEmbedding], Any] | None = None,
) -> dict[str, object]:
    """Build, verify, seal, and no-replace publish a fresh metadata-only index."""

    _validate_resolved(resolved)
    _require_atomic_no_replace()
    output = Path(output_path).expanduser().resolve(strict=False)
    if output.exists() or output.is_symlink():
        raise IsolatedIndexBuildError(f"output path already exists: {output}")
    protected = tuple(Path(value).expanduser().resolve(strict=False) for value in protected_paths)
    _validate_isolated_path(output, protected)
    protected_before = {str(path): _path_fingerprint(path) for path in protected if path.exists()}

    output.parent.mkdir(parents=True, exist_ok=True)
    run_id = str(uuid.uuid4())
    index_uuid = str(uuid.uuid4())
    staging = output.parent / f"{output.name}.building.{run_id}"
    _validate_isolated_path(staging, protected)
    staging.mkdir(mode=0o700)

    try:
        source = source_identity(zotero_client)
        version_before = _library_version(zotero_client)
        records = _freeze_records(zotero_client)
        version_after_freeze = _library_version(zotero_client)
        if version_before != version_after_freeze:
            raise IsolatedIndexBuildError("Zotero library changed while metadata documents were frozen")
    except Exception as exc:
        _write_failure(staging, run_id, "source-freeze", exc)
        raise

    ledger_path = staging / "input-ledger.json"
    ledger = {
        "schema_version": TEXT_POLICY_VERSION,
        "source_identity": source,
        "library_version": version_before,
        "records": records,
    }
    _write_json_fsync(ledger_path, ledger)
    source_scope_hash = _hash_json(ledger)
    descriptor = resolved.public_descriptor()
    dependency_versions = _dependency_versions()
    run_state_path = output.parent / f"{output.name}.run.{run_id}.json"
    run_state: dict[str, object] = {
        "schema_version": "zotero-mcp-index-coordinator/1.0.0",
        "status": "in-progress",
        "build_run_id": run_id,
        "index_uuid": index_uuid,
        "output": str(output),
        "source_identity": source,
        "zotero_library_version": version_before,
        "source_scope_hash": source_scope_hash,
        "eligible_count": len(records),
        "embedding": descriptor,
    }
    _write_json_fsync(run_state_path, run_state)
    collection_metadata: dict[str, str | int] = {
        "schema_version": COLLECTION_METADATA_SCHEMA,
        "index_uuid": index_uuid,
        "build_run_id": run_id,
        "embedding_provider": resolved.provider,
        "embedding_route": resolved.route,
        "embedding_model": resolved.model_name,
        "embedding_endpoint": resolved.endpoint,
        "embedding_task_type": resolved.task_type,
        "embedding_dimension": resolved.dimension,
        "embedding_producer": resolved.producer,
        "embedding_producer_version": resolved.producer_version,
        "embedding_descriptor_schema_version": resolved.descriptor_schema_version,
        "embedding_descriptor_hash": _hash_json(descriptor),
        "source_identity_hash": _hash_json(source),
        "source_scope_hash": source_scope_hash,
        "producer": "zotero-mcp",
        "producer_version": __version__,
        "chroma_version": dependency_versions["chromadb"],
        "google_genai_version": dependency_versions["google-genai"],
    }
    factory = embedding_factory or create_pinned_embedding_function
    writer_result_path = staging.parent / f".{staging.name}.writer.json"
    verifier_result_path = staging.parent / f".{staging.name}.verifier.json"
    try:
        _run_child(
            _writer_process,
            (ledger_path, staging, collection_name, resolved, collection_metadata, writer_result_path, factory),
            "writer",
        )
        _run_child(
            _verifier_process,
            (
                ledger_path,
                staging,
                collection_name,
                descriptor,
                collection_metadata,
                verifier_result_path,
            ),
            "verifier",
        )
        verifier_result = _read_json(verifier_result_path)
        version_after_verify = _library_version(zotero_client)
        if version_before != version_after_verify:
            raise IsolatedIndexBuildError("Zotero library changed while the isolated index was built")
        protected_after = {str(path): _path_fingerprint(path) for path in protected if path.exists()}
        if protected_before != protected_after:
            raise IsolatedIndexBuildError("a protected config or index path changed during the build")

        manifest: dict[str, object] = {
            "schema_version": BUILD_SCHEMA_VERSION,
            "status": "complete",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "producer": {
                "name": "zotero-mcp",
                "version": __version__,
                "dependencies": dependency_versions,
            },
            "coordinator_run_state": {
                "file": run_state_path.name,
                "source_scope_hash": source_scope_hash,
            },
            "index_identity": {
                "index_uuid": index_uuid,
                "build_run_id": run_id,
                "collection_name": collection_name,
                "collection_id": verifier_result["collection_id"],
            },
            "source_identity": source,
            "zotero_library_version": version_before,
            "source_scope_hash": source_scope_hash,
            "embedding": descriptor,
            "embedding_descriptor_hash": _hash_json(descriptor),
            "collection_metadata": collection_metadata,
            "collection_metadata_hash": _hash_json(collection_metadata),
            "counts": verifier_result["counts"],
            "hashes": verifier_result["hashes"],
            "validation": {"writer_process_exited": True, "verifier_process_exited": True},
        }
        _fsync_tree(staging)
        _write_json_fsync(staging / ".manifest.json.tmp", manifest)
        os.replace(staging / ".manifest.json.tmp", staging / "manifest.json")
        _fsync_directory(staging)
        _rename_no_replace(staging, output)
        _fsync_directory(output.parent)
        _write_json_fsync(
            output / "publication.json",
            {
                "schema_version": PUBLICATION_SCHEMA_VERSION,
                "status": "published",
                "manifest_sha256": _hash_json(manifest),
                "index_identity": manifest["index_identity"],
            },
        )
        _fsync_directory(output.parent)
        _write_json_fsync(
            run_state_path,
            run_state
            | {
                "status": "complete",
                "manifest_sha256": _hash_json(manifest),
            },
        )
        return manifest
    except BaseException as exc:
        failed_path = staging if staging.exists() else output if output.exists() else None
        if failed_path is not None:
            try:
                (failed_path / "publication.json").unlink()
            except FileNotFoundError:
                pass
            try:
                (failed_path / "manifest.json").unlink()
            except FileNotFoundError:
                pass
            _write_failure(failed_path, run_id, "build-verify-seal", exc)
        try:
            if run_state_path.exists():
                _write_json_fsync(run_state_path, run_state | {"status": "failed"})
        except OSError:
            pass
        raise
    finally:
        for result_path in (writer_result_path, verifier_result_path):
            try:
                result_path.unlink()
            except FileNotFoundError:
                pass


def _writer_process(
    ledger_path: Path,
    staging: Path,
    collection_name: str,
    resolved: ResolvedEmbedding,
    collection_metadata: Mapping[str, str | int],
    result_path: Path,
    embedding_factory: Callable[[ResolvedEmbedding], Any],
) -> None:
    from chromadb import PersistentClient  # pyright: ignore[reportMissingImports]
    from chromadb.config import Settings  # pyright: ignore[reportMissingImports]

    ledger = _read_json(ledger_path)
    records = ledger["records"]
    embedding_function = embedding_factory(resolved)
    if embedding_function.get_config() != resolved.public_descriptor():
        raise IsolatedIndexBuildError("embedding function descriptor differs from the pinned descriptor")
    client = PersistentClient(
        path=str(staging),
        settings=Settings(anonymized_telemetry=False, allow_reset=False),
    )
    collection = client.create_collection(
        name=collection_name,
        embedding_function=embedding_function,
        metadata=dict(collection_metadata),
    )
    for start in range(0, len(records), 25):
        batch = records[start:start + 25]
        documents = [record["document"] for record in batch]
        embeddings = embedding_function(documents)
        if hasattr(embeddings, "tolist"):
            embeddings = embeddings.tolist()
        if not isinstance(embeddings, (list, tuple)) or len(embeddings) != len(batch):
            raise IsolatedIndexBuildError("embedding provider returned an incomplete batch")
        validated_embeddings: list[list[float]] = []
        for vector in embeddings:
            if hasattr(vector, "tolist"):
                vector = vector.tolist()
            if not isinstance(vector, (list, tuple)) or len(vector) != TEXT_VECTOR_DIMENSION:
                raise IsolatedIndexBuildError("embedding provider returned a vector whose dimension is not 3072")
            numbers = [float(value) for value in vector]
            if not all(math.isfinite(value) for value in numbers):
                raise IsolatedIndexBuildError("embedding provider returned a non-finite vector")
            validated_embeddings.append(numbers)
        collection.add(
            ids=[record["key"] for record in batch],
            documents=documents,
            embeddings=validated_embeddings,
            metadatas=[record["metadata"] for record in batch],
        )
    _write_json_fsync(result_path, {"collection_id": str(collection.id), "count": collection.count()})


def _verifier_process(
    ledger_path: Path,
    staging: Path,
    collection_name: str,
    descriptor: Mapping[str, object],
    collection_metadata: Mapping[str, str | int],
    result_path: Path,
) -> None:
    from chromadb import PersistentClient  # pyright: ignore[reportMissingImports]
    from chromadb.config import Settings  # pyright: ignore[reportMissingImports]

    client = PersistentClient(
        path=str(staging),
        settings=Settings(anonymized_telemetry=False, allow_reset=False, migrations="validate"),
    )
    collection = client.get_collection(name=collection_name, embedding_function=None)
    stored_config = collection.configuration_json.get("embedding_function", {}).get("config")
    if stored_config != dict(descriptor):
        raise IsolatedIndexBuildError("persisted embedding descriptor does not match the pinned descriptor")
    if collection.metadata != dict(collection_metadata):
        raise IsolatedIndexBuildError("persisted collection metadata does not match the build identity")
    result = verify_collection_against_ledger(collection, ledger_path)
    _write_json_fsync(result_path, {"collection_id": str(collection.id), **result})


def verify_collection_against_ledger(collection: Any, ledger_path: str | Path) -> dict[str, object]:
    """Recompute exact sealed counts and hashes using two complete Chroma reads."""

    ledger = _read_json(Path(ledger_path))
    records = ledger.get("records")
    if not isinstance(records, list):
        raise IsolatedIndexBuildError("frozen input ledger records are invalid")
    expected: dict[str, Mapping[str, object]] = {}
    for record in records:
        if not isinstance(record, Mapping) or not isinstance(record.get("key"), str):
            raise IsolatedIndexBuildError("frozen input ledger contains an invalid record")
        key = str(record["key"])
        if key in expected:
            raise IsolatedIndexBuildError(f"frozen input ledger contains duplicate key: {key}")
        expected[key] = record
    first = _read_collection(collection)
    second = _read_collection(collection)
    first_hashes = _validate_records(expected, first)
    second_hashes = _validate_records(expected, second)
    if first_hashes != second_hashes:
        raise IsolatedIndexBuildError("Chroma records changed between complete verifier reads")
    return {
        "counts": {"expected": len(expected), "indexed": len(first)},
        "hashes": first_hashes,
    }


def _freeze_records(client: Any) -> list[dict[str, object]]:
    expected_count = client.num_items()
    if isinstance(expected_count, bool) or not isinstance(expected_count, int) or expected_count < 0:
        raise IsolatedIndexBuildError("Zotero top-level item count is invalid")
    records: list[dict[str, object]] = []
    seen: set[str] = set()
    raw_count = 0
    while raw_count < expected_count:
        batch = client.top(start=raw_count, limit=DEFAULT_PAGE_SIZE)
        if not isinstance(batch, list) or not batch:
            raise IsolatedIndexBuildError("Zotero pagination ended before the declared total")
        raw_count += len(batch)
        if raw_count > expected_count:
            raise IsolatedIndexBuildError("Zotero pagination exceeded the declared total")
        for item in batch:
            if not isinstance(item, Mapping) or not isinstance(item.get("data"), Mapping):
                raise IsolatedIndexBuildError("Zotero item is not an object")
            item_map = cast(Mapping[str, Any], item)
            data = dict(cast(Mapping[str, Any], item_map["data"]))
            if str(data.get("itemType", "")).lower() in EXCLUDED_ITEM_TYPES:
                continue
            top_key = item_map.get("key")
            data_key = data.get("key")
            if top_key is not None and data_key is not None and top_key != data_key:
                raise IsolatedIndexBuildError("Zotero item has conflicting top-level and data keys")
            key = top_key or data_key
            if not isinstance(key, str) or not key.strip():
                raise IsolatedIndexBuildError("eligible Zotero item has no key")
            if key in seen:
                raise IsolatedIndexBuildError(f"duplicate Zotero item key: {key}")
            seen.add(key)
            modified = data.get("dateModified")
            if not isinstance(modified, str) or not modified.strip():
                raise IsolatedIndexBuildError(f"Zotero dateModified is missing for {key}")
            document = metadata_document(item_map)
            records.append(
                {
                    "key": key,
                    "document": document,
                    "document_sha256": hashlib.sha256(document.encode("utf-8")).hexdigest(),
                    "metadata": {
                        "item_key": key,
                        "item_type": str(data.get("itemType", "")),
                        "title": str(data.get("title", "")),
                        "date_modified": modified,
                    },
                }
            )
    if raw_count != expected_count:
        raise IsolatedIndexBuildError("Zotero pagination was incomplete")
    if not records:
        raise IsolatedIndexBuildError("isolated index contains no eligible Zotero items")
    return sorted(records, key=lambda record: str(record["key"]))


def _read_collection(collection: Any) -> dict[str, dict[str, object]]:
    count = collection.count()
    result: dict[str, dict[str, object]] = {}
    for offset in range(0, max(count, 1), DEFAULT_PAGE_SIZE):
        response = collection.get(
            limit=DEFAULT_PAGE_SIZE,
            offset=offset,
            include=["documents", "embeddings", "metadatas"],
        )
        columns = [response.get(name) for name in ("ids", "documents", "embeddings", "metadatas")]
        if not all(isinstance(column, (list, tuple)) or hasattr(column, "tolist") for column in columns):
            raise IsolatedIndexBuildError("Chroma verifier response is incomplete")
        ids, documents, embeddings, metadatas = [
            list(column.tolist() if hasattr(column, "tolist") else column) for column in columns
        ]
        if not (len(ids) == len(documents) == len(embeddings) == len(metadatas)):
            raise IsolatedIndexBuildError("Chroma verifier columns have different lengths")
        for index, key in enumerate(ids):
            if not isinstance(key, str) or key in result:
                raise IsolatedIndexBuildError("Chroma contains an invalid or duplicate ID")
            result[key] = {
                "document": documents[index],
                "embedding": embeddings[index],
                "metadata": metadatas[index],
            }
        if len(ids) < DEFAULT_PAGE_SIZE:
            break
    if len(result) != count or collection.count() != count:
        raise IsolatedIndexBuildError("Chroma count changed or verifier pagination was incomplete")
    return result


def _validate_records(
    expected: Mapping[str, Mapping[str, object]], actual: Mapping[str, Mapping[str, object]]
) -> dict[str, str]:
    if set(expected) != set(actual):
        raise IsolatedIndexBuildError("expected and indexed Zotero key sets differ")
    document_hashes: dict[str, str] = {}
    vector_hashes: dict[str, str] = {}
    for key in sorted(expected):
        wanted = expected[key]
        found = actual[key]
        document = found.get("document")
        if not isinstance(document, str):
            raise IsolatedIndexBuildError(f"indexed document is missing for {key}")
        document_hash = hashlib.sha256(document.encode("utf-8")).hexdigest()
        if document_hash != wanted["document_sha256"]:
            raise IsolatedIndexBuildError(f"indexed document differs from frozen input for {key}")
        metadata = found.get("metadata")
        wanted_metadata = wanted.get("metadata")
        if not isinstance(wanted_metadata, Mapping):
            raise IsolatedIndexBuildError(f"frozen metadata is invalid for {key}")
        if not isinstance(metadata, Mapping) or metadata.get("date_modified") != wanted_metadata.get("date_modified"):
            raise IsolatedIndexBuildError(f"indexed date_modified differs from frozen input for {key}")
        vector = found.get("embedding")
        if isinstance(vector, _ArrayLike):
            vector = vector.tolist()
        if not isinstance(vector, (list, tuple)) or len(vector) != TEXT_VECTOR_DIMENSION:
            raise IsolatedIndexBuildError(f"indexed vector dimension is not 3072 for {key}")
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in vector):
            raise IsolatedIndexBuildError(f"indexed vector is non-numeric for {key}")
        numbers = [float(value) for value in vector]
        if not all(math.isfinite(value) for value in numbers):
            raise IsolatedIndexBuildError(f"indexed vector is non-finite for {key}")
        document_hashes[key] = document_hash
        vector_hashes[key] = hashlib.sha256(struct.pack(f"<{TEXT_VECTOR_DIMENSION}d", *numbers)).hexdigest()
    return {
        "key_set_sha256": _hash_json(sorted(expected)),
        "documents_sha256": _hash_json(document_hashes),
        "vectors_sha256": _hash_json(vector_hashes),
    }


def _run_child(target: Callable[..., None], args: tuple[Any, ...], label: str) -> None:
    process = multiprocessing.get_context("spawn").Process(
        target=_sanitized_child_entry,
        args=(target, args, label),
        daemon=False,
    )
    process.start()
    process.join()
    if process.exitcode != 0:
        raise IsolatedIndexBuildError(f"isolated Chroma {label} subprocess failed with exit code {process.exitcode}")


def _sanitized_child_entry(target: Callable[..., None], args: tuple[Any, ...], label: str) -> None:
    try:
        target(*args)
    except BaseException:
        print(f"isolated Chroma {label} child failed", file=sys.stderr)
        raise SystemExit(1) from None


def _validate_resolved(resolved: ResolvedEmbedding) -> None:
    if resolved.provider != "gemini" or resolved.route != "gemini-direct":
        raise IsolatedIndexBuildError("isolated embedding provider route is unsupported")
    if resolved.task_type != "RETRIEVAL_DOCUMENT" or resolved.dimension != TEXT_VECTOR_DIMENSION:
        raise IsolatedIndexBuildError("isolated embeddings must be RETRIEVAL_DOCUMENT with dimension 3072")
    if resolved.model_name not in ISOLATED_3072_MODELS or not resolved.endpoint:
        raise IsolatedIndexBuildError("isolated embedding descriptor is incomplete")
    if (
        resolved.producer != "zotero-mcp"
        or resolved.producer_version != __version__
        or resolved.descriptor_schema_version != EMBEDDING_DESCRIPTOR_SCHEMA_VERSION
    ):
        raise IsolatedIndexBuildError("isolated embedding producer descriptor is invalid")


def _validate_isolated_path(candidate: Path, protected: Sequence[Path]) -> None:
    for path in protected:
        if candidate == path or candidate.is_relative_to(path) or path.is_relative_to(candidate):
            raise IsolatedIndexBuildError(f"isolated output overlaps protected path: {path}")


def _library_version(client: Any) -> int:
    try:
        value = client.last_modified_version()
    except Exception as exc:
        raise IsolatedIndexBuildError("cannot read Zotero library version") from exc
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise IsolatedIndexBuildError("Zotero library version is invalid")
    return value


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _hash_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _write_json_fsync(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(_canonical_json(value))
            stream.write(b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _write_failure(staging: Path, run_id: str, phase: str, exc: BaseException) -> None:
    """Persist a credential-free diagnostic without ever creating a complete manifest."""

    _write_json_fsync(
        staging / "failure.json",
        {
            "status": "failed",
            "build_run_id": run_id,
            "phase": phase,
            "error_type": type(exc).__name__,
        },
    )


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise IsolatedIndexBuildError(f"JSON object expected at {path}")
    return value


def _path_fingerprint(path: Path) -> str:
    if path.is_symlink():
        raise IsolatedIndexBuildError(f"protected path is a symlink: {path}")
    if path.is_file():
        return _hash_file(path)
    if not path.is_dir():
        raise IsolatedIndexBuildError(f"protected path is not a regular file or directory: {path}")
    entries: list[dict[str, object]] = []
    for entry in sorted(path.rglob("*"), key=lambda item: item.relative_to(path).as_posix()):
        relative = entry.relative_to(path).as_posix()
        if entry.is_symlink():
            raise IsolatedIndexBuildError(f"protected directory contains a symlink: {relative}")
        if entry.is_dir():
            continue
        if not entry.is_file():
            raise IsolatedIndexBuildError(f"protected directory contains a special file: {relative}")
        entries.append({"path": relative, "size": entry.stat().st_size, "sha256": _hash_file(entry)})
    return _hash_json(entries)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _dependency_versions() -> dict[str, str]:
    try:
        return {
            "chromadb": importlib.metadata.version("chromadb"),
            "google-genai": importlib.metadata.version("google-genai"),
        }
    except importlib.metadata.PackageNotFoundError as exc:
        raise IsolatedIndexBuildError(f"required isolated build dependency is unavailable: {exc.name}") from exc


def _fsync_tree(root: Path) -> None:
    directories: list[Path] = [root]
    for entry in root.rglob("*"):
        if entry.is_symlink():
            raise IsolatedIndexBuildError(f"staging tree contains a symlink: {entry}")
        if entry.is_file():
            fd = os.open(entry, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        elif entry.is_dir():
            directories.append(entry)
        else:
            raise IsolatedIndexBuildError(f"staging tree contains a special file: {entry}")
    for directory in sorted(directories, key=lambda value: len(value.parts), reverse=True):
        _fsync_directory(directory)


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _rename_no_replace(source: Path, destination: Path) -> None:
    renameat2 = _require_atomic_no_replace()
    result = renameat2(-100, os.fsencode(source), -100, os.fsencode(destination), 1)
    if result != 0:
        errno = ctypes.get_errno()
        raise IsolatedIndexBuildError(f"atomic no-replace publication failed: {os.strerror(errno)}")


def _require_atomic_no_replace() -> Any:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise IsolatedIndexBuildError("atomic no-replace directory publication is unavailable")
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    return renameat2


__all__ = [
    "BUILD_SCHEMA_VERSION",
    "IsolatedIndexBuildError",
    "build_isolated_index",
    "metadata_document",
    "source_identity",
    "verify_collection_against_ledger",
]

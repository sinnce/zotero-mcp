"""Read-only, deterministic Zotero metadata and Chroma vector export.

The exporter is the producer side of Paper Graph's ``storage_build`` snapshot
contract.  It deliberately accepts already-created clients so tests and other
callers can prove that the operation uses only read methods.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

EXCLUDED_ITEM_TYPES = frozenset({"attachment", "note", "annotation"})
SNAPSHOT_SCHEMA_VERSION = "zotero-mcp-snapshot/1.0.0"
TEXT_VECTOR_DIMENSION = 3072
DEFAULT_PAGE_SIZE = 100
DEFAULT_MAX_PAGES = 100_000


@runtime_checkable
class _ArrayLike(Protocol):
    def tolist(self) -> object: ...


class SnapshotExportError(RuntimeError):
    """The producer could not prove a complete, consistent snapshot."""

    code = "snapshot_export_failed"


class SnapshotCompletenessError(SnapshotExportError):
    code = "snapshot_incomplete"


class SnapshotDriftError(SnapshotExportError):
    code = "snapshot_source_drift"


class SnapshotProvenanceError(SnapshotExportError):
    code = "snapshot_provenance_unknown"


@dataclass(frozen=True, slots=True)
class EmbeddingProvenance:
    """Verified producer facts attached to every exported text vector."""

    model: str
    dimension: int
    producer: str = "zotero-mcp"
    model_version: str | None = None
    producer_version: str | None = None

    def validate(self) -> None:
        if not isinstance(self.model, str) or not self.model.strip():
            raise SnapshotProvenanceError("embedding model is unknown")
        if self.producer != "zotero-mcp":
            raise SnapshotProvenanceError("text vectors must be attributed to zotero-mcp")
        if self.dimension != TEXT_VECTOR_DIMENSION:
            raise SnapshotProvenanceError(
                f"text vector dimension must be {TEXT_VECTOR_DIMENSION}, got {self.dimension}"
            )


def export_snapshot(
    zotero_client: Any,
    chroma_collection: Any,
    provenance: EmbeddingProvenance,
    *,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_pages: int = DEFAULT_MAX_PAGES,
    retrieved_at: datetime | None = None,
) -> dict[str, object]:
    """Export one complete read-only producer snapshot.

    Zotero's library version brackets metadata pagination.  Chroma is read
    twice and the exact record fingerprint must agree, detecting additions,
    removals, document changes, metadata changes, and vector changes while an
    export is in progress.
    """

    _validate_bounds(page_size, max_pages)
    provenance.validate()
    scope = _source_scope(zotero_client, chroma_collection)

    library_version_before = _library_version(zotero_client)
    papers = _read_zotero_papers(zotero_client, page_size=page_size, max_pages=max_pages)
    library_version_after_metadata = _library_version(zotero_client)
    if library_version_before != library_version_after_metadata:
        raise SnapshotDriftError(
            f"Zotero library changed during export ({library_version_before} -> {library_version_after_metadata})"
        )

    first_index = _read_chroma_records(chroma_collection, page_size=page_size, max_pages=max_pages)
    second_index = _read_chroma_records(chroma_collection, page_size=page_size, max_pages=max_pages)
    if _stable_hash(first_index) != _stable_hash(second_index):
        raise SnapshotDriftError("Chroma collection changed during export")
    library_version_after = _library_version(zotero_client)
    if library_version_before != library_version_after:
        raise SnapshotDriftError(
            f"Zotero library changed while vectors were exported ({library_version_before} -> {library_version_after})"
        )

    paper_keys = {str(item["key"]) for item in papers}
    index_keys = set(first_index)
    missing = sorted(paper_keys - index_keys)
    extra = sorted(index_keys - paper_keys)
    if missing or extra:
        parts: list[str] = []
        if missing:
            parts.append("missing indexed vectors: " + ", ".join(missing))
        if extra:
            parts.append("indexed IDs without exported papers: " + ", ".join(extra))
        raise SnapshotCompletenessError("; ".join(parts))
    if not papers:
        raise SnapshotCompletenessError("snapshot contains no eligible Zotero papers")
    _validate_index_freshness(papers, first_index)

    embeddings = [_embedding_record(key, first_index[key], provenance) for key in sorted(first_index)]
    stable_metadata = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "source_scope": scope,
        "zotero_library_version": library_version_after,
        "excluded_item_types": sorted(EXCLUDED_ITEM_TYPES),
        "embedding": _provenance_mapping(provenance),
        "counts": {"papers": len(papers), "embeddings": len(embeddings)},
    }
    stable_payload = {
        "metadata": stable_metadata,
        "papers": sorted(papers, key=lambda item: str(item["key"])),
        "embeddings": embeddings,
    }
    snapshot_hash = "sha256:" + _stable_hash(stable_payload)
    timestamp = retrieved_at or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        raise SnapshotExportError("retrieved_at must be timezone-aware")
    timestamp = timestamp.astimezone(timezone.utc)
    return {
        "snapshot_id": f"zotero-mcp:{snapshot_hash[7:23]}",
        "snapshot_hash": snapshot_hash,
        "metadata": stable_metadata | {"retrieved_at": timestamp.isoformat()},
        "papers": stable_payload["papers"],
        "embeddings": embeddings,
    }


def export_configured_snapshot(config_path: str | Path) -> dict[str, object]:
    """Open configured producer sources without creating or updating an index."""

    path = Path(config_path).expanduser().resolve()
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SnapshotExportError(f"cannot read Zotero MCP config {path}: {exc}") from exc
    semantic = config.get("semantic_search")
    if not isinstance(semantic, Mapping):
        raise SnapshotProvenanceError("semantic_search configuration is missing")

    from chromadb import PersistentClient  # pyright: ignore[reportMissingImports]
    from chromadb.config import Settings  # pyright: ignore[reportMissingImports]

    from zotero_mcp._version import __version__
    from zotero_mcp.client import get_zotero_client
    from zotero_mcp.config_paths import get_chroma_db_path

    persist_directory = get_chroma_db_path()
    collection_name = semantic.get("collection_name") or "zotero_library"
    if not isinstance(persist_directory, (str, Path)):
        raise SnapshotCompletenessError("configured Chroma path is invalid")
    chroma_path = Path(persist_directory).expanduser().resolve()
    if not chroma_path.is_dir() or not (chroma_path / "chroma.sqlite3").is_file():
        raise SnapshotCompletenessError(f"configured Chroma database does not exist: {chroma_path}")
    source_fingerprint = _directory_fingerprint(chroma_path)
    with tempfile.TemporaryDirectory(prefix="zotero-mcp-snapshot-") as temporary_directory:
        clone_path = Path(temporary_directory) / "chroma"
        shutil.copytree(chroma_path, clone_path)
        if _directory_fingerprint(chroma_path) != source_fingerprint:
            raise SnapshotDriftError("Chroma source changed while its read-only clone was created")
        chroma = PersistentClient(
            path=str(clone_path),
            settings=Settings(
                anonymized_telemetry=False,
                allow_reset=False,
                migrations="validate",
            ),
        )
        try:
            collection = chroma.get_collection(name=str(collection_name), embedding_function=None)
        except Exception as exc:
            raise SnapshotCompletenessError(f"configured Chroma collection does not exist: {collection_name}") from exc

        configured_model = _configured_model_name(semantic)
        persisted_model = _persisted_model_name(collection)
        if configured_model != persisted_model:
            raise SnapshotProvenanceError(
                f"configured and persisted embedding models differ: {configured_model!r} != {persisted_model!r}"
            )
        provenance = EmbeddingProvenance(
            model=persisted_model,
            dimension=TEXT_VECTOR_DIMENSION,
            producer_version=__version__,
        )
        snapshot = export_snapshot(get_zotero_client(), collection, provenance)
    if _directory_fingerprint(chroma_path) != source_fingerprint:
        raise SnapshotDriftError("Chroma source changed during export")
    return snapshot


def write_snapshot_atomic(snapshot: Mapping[str, object], output_path: str | Path) -> None:
    """Durably replace a snapshot JSON file without exposing partial output."""

    target = Path(output_path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(snapshot, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        directory_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary.exists():
            temporary.unlink()


def _read_zotero_papers(client: Any, *, page_size: int, max_pages: int) -> list[dict[str, object]]:
    expected_count = client.num_items()
    if isinstance(expected_count, bool) or not isinstance(expected_count, int) or expected_count < 0:
        raise SnapshotCompletenessError("Zotero top-level item count is invalid")
    result: list[dict[str, object]] = []
    seen: set[str] = set()
    raw_count = 0
    for page in range(max_pages):
        batch = client.top(start=page * page_size, limit=page_size)
        if not isinstance(batch, list):
            raise SnapshotCompletenessError("Zotero top-level items page is not a list")
        raw_count += len(batch)
        for raw in batch:
            if not isinstance(raw, Mapping):
                raise SnapshotCompletenessError("Zotero item is not an object")
            item = dict(raw)
            data = item.get("data")
            if not isinstance(data, Mapping):
                raise SnapshotCompletenessError("Zotero item data is not an object")
            if str(data.get("itemType", "")).lower() in EXCLUDED_ITEM_TYPES:
                continue
            key = item.get("key") or data.get("key")
            if not isinstance(key, str) or not key.strip():
                raise SnapshotCompletenessError("eligible Zotero item has no key")
            if key in seen:
                raise SnapshotCompletenessError(f"duplicate Zotero item key: {key}")
            seen.add(key)
            item["key"] = key
            result.append(item)
        if raw_count > expected_count:
            raise SnapshotDriftError(f"Zotero pagination exceeded the declared total ({raw_count} > {expected_count})")
        if raw_count == expected_count:
            return result
        if len(batch) < page_size:
            if raw_count != expected_count:
                raise SnapshotCompletenessError(
                    f"Zotero pagination ended before the declared total ({raw_count} != {expected_count})"
                )
            return result
    raise SnapshotCompletenessError("Zotero pagination exceeded the configured page budget")


def _read_chroma_records(collection: Any, *, page_size: int, max_pages: int) -> dict[str, dict[str, object]]:
    expected_count = collection.count()
    if not isinstance(expected_count, int) or expected_count < 0:
        raise SnapshotCompletenessError("Chroma collection count is invalid")
    records: dict[str, dict[str, object]] = {}
    for page in range(max_pages):
        response = collection.get(
            limit=page_size,
            offset=page * page_size,
            include=["documents", "embeddings", "metadatas"],
        )
        if not isinstance(response, Mapping):
            raise SnapshotCompletenessError("Chroma page is not an object")
        ids = _sequence(response.get("ids"), "ids")
        documents = _sequence(response.get("documents"), "documents")
        embeddings = _sequence(response.get("embeddings"), "embeddings")
        metadatas = _sequence(response.get("metadatas"), "metadatas")
        if not (len(ids) == len(documents) == len(embeddings) == len(metadatas)):
            raise SnapshotCompletenessError("Chroma page columns have different lengths")
        for index, raw_id in enumerate(ids):
            if not isinstance(raw_id, str) or not raw_id.strip():
                raise SnapshotCompletenessError("Chroma record has no ID")
            if raw_id in records:
                raise SnapshotCompletenessError(f"duplicate Chroma ID: {raw_id}")
            document = documents[index]
            if not isinstance(document, str):
                raise SnapshotCompletenessError(f"Chroma document is missing for {raw_id}")
            raw_metadata = metadatas[index]
            if raw_metadata is not None and not isinstance(raw_metadata, Mapping):
                raise SnapshotCompletenessError(f"Chroma metadata is invalid for {raw_id}")
            records[raw_id] = {
                "document": document,
                "vector": _vector(embeddings[index], raw_id),
                "metadata": dict(raw_metadata or {}),
            }
        if len(ids) < page_size:
            break
    else:
        raise SnapshotCompletenessError("Chroma pagination exceeded the configured page budget")
    if len(records) != expected_count or collection.count() != expected_count:
        raise SnapshotDriftError(
            f"Chroma count changed or pagination was incomplete ({expected_count} != {len(records)})"
        )
    return {key: records[key] for key in sorted(records)}


def _embedding_record(key: str, record: Mapping[str, object], provenance: EmbeddingProvenance) -> dict[str, object]:
    document = str(record["document"])
    source_hash = "sha256:" + hashlib.sha256(document.encode("utf-8")).hexdigest()
    indexed_metadata = record.get("metadata")
    metadata: dict[str, object] = {
        "document_id": key,
        "dimension": provenance.dimension,
    }
    if isinstance(indexed_metadata, Mapping) and indexed_metadata.get("version") is not None:
        metadata["indexed_item_version"] = indexed_metadata["version"]
    if provenance.model_version:
        metadata["model_version"] = provenance.model_version
    if provenance.producer_version:
        metadata["producer_version"] = provenance.producer_version
    return {
        "paper_key": key,
        "view_name": "text_retrieval",
        "vector": record["vector"],
        "producer": provenance.producer,
        "model": provenance.model,
        "dimension": provenance.dimension,
        "source_hash": source_hash,
        "metadata": metadata,
    }


def _validate_index_freshness(papers: list[dict[str, object]], index: Mapping[str, Mapping[str, object]]) -> None:
    for paper in papers:
        key = str(paper["key"])
        data = paper.get("data")
        current_modified = data.get("dateModified") if isinstance(data, Mapping) else None
        indexed_metadata = index[key].get("metadata")
        indexed_modified = indexed_metadata.get("date_modified") if isinstance(indexed_metadata, Mapping) else None
        if not isinstance(current_modified, str) or not current_modified.strip():
            raise SnapshotCompletenessError(f"Zotero dateModified is missing for {key}")
        if not isinstance(indexed_modified, str) or not indexed_modified.strip():
            raise SnapshotCompletenessError(f"indexed date_modified is missing for {key}")
        if current_modified != indexed_modified:
            raise SnapshotCompletenessError(
                f"indexed document is stale for {key}: {indexed_modified!r} != {current_modified!r}"
            )


def _vector(value: object, record_id: str) -> list[float]:
    if isinstance(value, _ArrayLike):
        value = value.tolist()
    if not isinstance(value, (list, tuple)):
        raise SnapshotCompletenessError(f"Chroma embedding is missing for {record_id}")
    if len(value) != TEXT_VECTOR_DIMENSION:
        raise SnapshotCompletenessError(
            f"Chroma embedding dimension for {record_id} is {len(value)}, expected {TEXT_VECTOR_DIMENSION}"
        )
    result: list[float] = []
    for component in value:
        if isinstance(component, bool) or not isinstance(component, (int, float)):
            raise SnapshotCompletenessError(f"Chroma embedding is non-numeric for {record_id}")
        number = float(component)
        if not math.isfinite(number):
            raise SnapshotCompletenessError(f"Chroma embedding is non-finite for {record_id}")
        result.append(number)
    return result


def _library_version(client: Any) -> int:
    try:
        value = client.last_modified_version()
    except Exception as exc:
        raise SnapshotDriftError("cannot read Zotero library version") from exc
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SnapshotDriftError("Zotero library version is invalid")
    return value


def _source_scope(zotero_client: Any, collection: Any) -> dict[str, str]:
    library_id = getattr(zotero_client, "library_id", None)
    library_type = getattr(zotero_client, "library_type", None)
    collection_name = getattr(collection, "name", None) or getattr(collection, "collection_name", None)
    collection_id = getattr(collection, "id", None)
    values = (library_id, library_type, collection_name, collection_id)
    if not all(value is not None and str(value).strip() for value in values):
        raise SnapshotCompletenessError("source library or Chroma collection scope is unknown")
    return {
        "library_id": str(library_id),
        "library_type": str(library_type),
        "chroma_collection": str(collection_name),
        "chroma_collection_id": str(collection_id),
    }


def _configured_model_name(config: Mapping[str, object]) -> str:
    provider = os.getenv("ZOTERO_EMBEDDING_MODEL") or config.get("embedding_model")
    details = config.get("embedding_config")
    if not isinstance(provider, str) or not provider.strip():
        raise SnapshotProvenanceError("configured embedding provider is unknown")
    detail_map = details if isinstance(details, Mapping) else {}
    if provider == "openai" and os.getenv("OPENAI_API_KEY"):
        return os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
    if provider == "gemini" and (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")):
        return os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")
    if provider == "default":
        return "all-MiniLM-L6-v2"
    if provider not in {"openai", "gemini", "qwen", "embeddinggemma"}:
        return provider
    provider_defaults = {
        "gemini": os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001"),
        "openai": os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"),
        "qwen": "Qwen/Qwen3-Embedding-0.6B",
        "embeddinggemma": "google/embeddinggemma-300m",
    }
    model = detail_map.get("model_name") or provider_defaults[provider]
    if not isinstance(model, str) or not model.strip():
        raise SnapshotProvenanceError("configured embedding model is unknown")
    return model


def _directory_fingerprint(path: Path) -> str:
    records: list[dict[str, object]] = []
    try:
        entries = sorted(path.rglob("*"), key=lambda value: value.relative_to(path).as_posix())
        for entry in entries:
            relative = entry.relative_to(path).as_posix()
            if entry.is_symlink():
                raise SnapshotCompletenessError(f"Chroma source contains a symlink: {relative}")
            if entry.is_dir():
                continue
            if not entry.is_file():
                raise SnapshotCompletenessError(f"Chroma source contains a special file: {relative}")
            digest = hashlib.sha256()
            size = 0
            with entry.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
            records.append({"path": relative, "size": size, "sha256": digest.hexdigest()})
    except OSError as exc:
        raise SnapshotDriftError(f"cannot fingerprint Chroma source: {exc}") from exc
    return _stable_hash(records)


def _persisted_model_name(collection: Any) -> str:
    candidates = [
        getattr(collection, "configuration_json", None),
        getattr(collection, "metadata", None),
    ]
    for candidate in candidates:
        model = _find_model_name(candidate)
        if model:
            return model
    raise SnapshotProvenanceError("persisted Chroma embedding model is unknown")


def _find_model_name(value: object) -> str | None:
    if isinstance(value, Mapping):
        for key in ("model_name", "model"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate
        for candidate in value.values():
            found = _find_model_name(candidate)
            if found:
                return found
    elif isinstance(value, (list, tuple)):
        for candidate in value:
            found = _find_model_name(candidate)
            if found:
                return found
    return None


def _provenance_mapping(value: EmbeddingProvenance) -> dict[str, object]:
    return {
        "producer": value.producer,
        "producer_version": value.producer_version,
        "model": value.model,
        "model_version": value.model_version,
        "dimension": value.dimension,
    }


def _sequence(value: object, name: str) -> list[Any]:
    if isinstance(value, _ArrayLike):
        value = value.tolist()
    if not isinstance(value, (list, tuple)):
        raise SnapshotCompletenessError(f"Chroma {name} column is missing")
    return list(value)


def _stable_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _validate_bounds(page_size: int, max_pages: int) -> None:
    if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 1000:
        raise ValueError("page_size must be an integer from 1 to 1000")
    if isinstance(max_pages, bool) or not isinstance(max_pages, int) or max_pages < 1:
        raise ValueError("max_pages must be a positive integer")


__all__ = [
    "EmbeddingProvenance",
    "SnapshotCompletenessError",
    "SnapshotDriftError",
    "SnapshotExportError",
    "SnapshotProvenanceError",
    "export_configured_snapshot",
    "export_snapshot",
    "write_snapshot_atomic",
]

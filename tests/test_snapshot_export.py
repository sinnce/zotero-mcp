from __future__ import annotations

import hashlib
import json
import os
import sys
import types
from datetime import datetime, timezone

import pytest

from zotero_mcp.snapshot_export import (
    EmbeddingProvenance,
    SnapshotCompletenessError,
    SnapshotDriftError,
    SnapshotProvenanceError,
    _configured_model_name,
    export_configured_snapshot,
    export_snapshot,
    write_snapshot_atomic,
)


def _item(key: str, item_type: str = "journalArticle") -> dict:
    return {
        "key": key,
        "version": 7,
        "data": {
            "key": key,
            "itemType": item_type,
            "title": f"Title {key}",
            "abstractNote": f"Abstract {key}",
            "dateModified": "2026-09-08T00:00:00Z",
            "creators": [
                {"creatorType": "author", "firstName": "Ada", "lastName": "Lovelace"},
                {"creatorType": "editor", "name": "Raw Organization"},
            ],
            "tags": [{"tag": "graph"}],
            "collections": ["COLLECTION1"],
        },
    }


class FakeZotero:
    library_id = "0"
    library_type = "user"

    def __init__(self, items, versions=(12, 12, 12)):
        self._items = items
        self._versions = iter(versions)
        self.calls = []

    def last_modified_version(self):
        self.calls.append(("last_modified_version",))
        return next(self._versions)

    def num_items(self):
        self.calls.append(("num_items",))
        return len(self._items)

    def top(self, *, start, limit):
        self.calls.append(("top", start, limit))
        return self._items[start : start + limit]


class FakeCollection:
    name = "zotero_library"
    id = "11111111-2222-3333-4444-555555555555"

    def __init__(self, records, *, second_pass=None, count_sequence=None):
        self._records = records
        self._second_pass = second_pass
        self._count_sequence = iter(count_sequence) if count_sequence else None
        self.get_calls = 0

    def count(self):
        if self._count_sequence is not None:
            return next(self._count_sequence)
        return len(self._records)

    def get(self, *, limit, offset, include):
        assert include == ["documents", "embeddings", "metadatas"]
        self.get_calls += 1
        active = self._records
        if self._second_pass is not None and self.get_calls > 2:
            active = self._second_pass
        rows = active[offset : offset + limit]
        return {
            "ids": [row[0] for row in rows],
            "documents": [row[1] for row in rows],
            "embeddings": [row[2] for row in rows],
            "metadatas": [row[3] for row in rows],
        }


def _record(key: str, document: str | None = None, vector=None):
    return (
        key,
        document or f"indexed document {key}",
        vector or [0.25] * 3072,
        {"version": 7, "date_modified": "2026-09-08T00:00:00Z"},
    )


def _export(zotero, collection, **kwargs):
    return export_snapshot(
        zotero,
        collection,
        EmbeddingProvenance(
            model="gemini-embedding-2-preview",
            dimension=3072,
            producer_version="9.9.9",
        ),
        retrieved_at=datetime(2026, 9, 8, tzinfo=timezone.utc),
        **kwargs,
    )


def test_exports_full_paginated_contract_without_mutating_sources():
    zotero = FakeZotero([_item("A"), _item("ATT", "attachment"), _item("N", "note"), _item("B")])
    collection = FakeCollection([_record("A"), _record("B")])

    snapshot = _export(zotero, collection, page_size=2)

    assert [paper["key"] for paper in snapshot["papers"]] == ["A", "B"]
    assert snapshot["papers"][0]["data"]["creators"][1] == {
        "creatorType": "editor",
        "name": "Raw Organization",
    }
    assert snapshot["metadata"]["source_scope"] == {
        "library_id": "0",
        "library_type": "user",
        "chroma_collection": "zotero_library",
        "chroma_collection_id": "11111111-2222-3333-4444-555555555555",
    }
    assert zotero.calls == [
        ("last_modified_version",),
        ("num_items",),
        ("top", 0, 2),
        ("top", 2, 2),
        ("last_modified_version",),
        ("last_modified_version",),
    ]


def test_source_hash_is_exact_utf8_indexed_document_hash():
    document = " exact\nindexed text  "
    snapshot = _export(FakeZotero([_item("A")]), FakeCollection([_record("A", document)]))
    expected = "sha256:" + hashlib.sha256(document.encode("utf-8")).hexdigest()

    assert snapshot["embeddings"][0]["source_hash"] == expected
    assert snapshot["embeddings"][0]["vector"] == [0.25] * 3072


@pytest.mark.parametrize(
    ("papers", "records", "message"),
    [
        ([_item("A"), _item("B")], [_record("A")], "missing indexed vectors: B"),
        ([_item("A")], [_record("A"), _record("B")], "indexed IDs without exported papers: B"),
    ],
)
def test_fails_closed_on_missing_or_extra_index_ids(papers, records, message):
    with pytest.raises(SnapshotCompletenessError, match=message):
        _export(FakeZotero(papers), FakeCollection(records))


@pytest.mark.parametrize(
    ("vector", "message"),
    [
        ([0.1] * 3, "dimension"),
        ([0.1] * 3071 + [float("nan")], "non-finite"),
    ],
)
def test_fails_closed_on_invalid_vector(vector, message):
    with pytest.raises(SnapshotCompletenessError, match=message):
        _export(FakeZotero([_item("A")]), FakeCollection([_record("A", vector=vector)]))


def test_detects_zotero_library_drift():
    with pytest.raises(SnapshotDriftError, match="Zotero library changed"):
        _export(FakeZotero([_item("A")], versions=(12, 13, 13)), FakeCollection([_record("A")]))


def test_rejects_premature_short_zotero_page():
    zotero = FakeZotero([_item("A")])
    zotero.num_items = lambda: 2
    with pytest.raises(SnapshotCompletenessError, match="ended before the declared total"):
        _export(zotero, FakeCollection([_record("A")]))


def test_rejects_stale_indexed_document_metadata():
    paper = _item("A")
    paper["data"]["dateModified"] = "2026-09-08T01:00:00Z"
    with pytest.raises(SnapshotCompletenessError, match="indexed document is stale"):
        _export(FakeZotero([paper]), FakeCollection([_record("A", "OLD")]))


def test_detects_same_count_chroma_document_or_vector_drift():
    collection = FakeCollection(
        [_record("A", "first")],
        second_pass=[_record("A", "changed")],
    )
    with pytest.raises(SnapshotDriftError, match="Chroma collection changed"):
        _export(FakeZotero([_item("A")]), collection, page_size=1)


def test_snapshot_hash_is_stable_across_retrieval_times():
    first = _export(FakeZotero([_item("A")]), FakeCollection([_record("A")]))
    second = export_snapshot(
        FakeZotero([_item("A")]),
        FakeCollection([_record("A")]),
        EmbeddingProvenance("gemini-embedding-2-preview", 3072, producer_version="9.9.9"),
        retrieved_at=datetime(2026, 9, 9, tzinfo=timezone.utc),
    )

    assert first["snapshot_hash"] == second["snapshot_hash"]
    assert first["snapshot_id"] == second["snapshot_id"]
    assert first["metadata"]["retrieved_at"] != second["metadata"]["retrieved_at"]


def test_snapshot_hash_binds_sealed_build_metadata():
    first = _export(
        FakeZotero([_item("A")]),
        FakeCollection([_record("A")]),
        sealed_build={"index_identity": {"index_uuid": "first"}},
    )
    second = _export(
        FakeZotero([_item("A")]),
        FakeCollection([_record("A")]),
        sealed_build={"index_identity": {"index_uuid": "second"}},
    )

    assert first["snapshot_hash"] != second["snapshot_hash"]


def test_rejects_untruthful_or_incompatible_provenance():
    with pytest.raises(SnapshotProvenanceError, match="dimension"):
        export_snapshot(
            FakeZotero([_item("A")]),
            FakeCollection([_record("A")]),
            EmbeddingProvenance("unknown", 384),
        )


def test_atomic_writer_emits_machine_readable_json(tmp_path):
    target = tmp_path / "snapshot.json"
    target.write_text("old", encoding="utf-8")
    snapshot = _export(FakeZotero([_item("A")]), FakeCollection([_record("A")]))

    write_snapshot_atomic(snapshot, target)

    assert json.loads(target.read_text(encoding="utf-8")) == snapshot
    assert list(tmp_path.iterdir()) == [target]


def test_configured_export_uses_environment_sources_and_opens_only_a_clone(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "client_env": {"ZOTERO_LOCAL_PORT": "23119"},
                "semantic_search": {
                    "collection_name": "zotero_library",
                    "embedding_model": "gemini",
                    "embedding_config": {"model_name": "gemini-embedding-2-preview"},
                    "persist_directory": str(tmp_path / "stale-unsupported-path"),
                },
            }
        ),
        encoding="utf-8",
    )
    collection = FakeCollection([_record("A")])
    collection.configuration_json = {
        "embedding_function": {
            "name": "google_generative_ai",
            "config": {"model_name": "gemini-embedding-2-preview"},
        }
    }
    calls = []

    class PersistentClient:
        def __init__(self, *, path, settings):
            calls.append(("open", path, settings.allow_reset))
            with open(f"{path}/chroma.sqlite3", "ab") as stream:
                stream.write(b"clone-opened")

        def get_collection(self, *, name, embedding_function):
            calls.append(("get_collection", name, embedding_function))
            return collection

    chromadb_module = types.ModuleType("chromadb")
    chromadb_module.PersistentClient = PersistentClient
    config_module = types.ModuleType("chromadb.config")

    class Settings:
        def __init__(self, *, anonymized_telemetry, allow_reset, migrations):
            assert anonymized_telemetry is False
            assert migrations == "validate"
            self.allow_reset = allow_reset

    config_module.Settings = Settings
    monkeypatch.setitem(sys.modules, "chromadb", chromadb_module)
    monkeypatch.setitem(sys.modules, "chromadb.config", config_module)
    canonical_chroma = tmp_path / "environment-chroma"
    monkeypatch.setenv("ZOTERO_MCP_CHROMA_DB_PATH", str(canonical_chroma))
    monkeypatch.setenv("ZOTERO_LOCAL_PORT", "23120")

    def configured_client():
        calls.append(("zotero_port", os.environ["ZOTERO_LOCAL_PORT"]))
        return FakeZotero([_item("A")])

    monkeypatch.setattr("zotero_mcp.client.get_zotero_client", configured_client)
    monkeypatch.setattr(
        "zotero_mcp.config_paths.get_chroma_db_path",
        lambda: os.environ["ZOTERO_MCP_CHROMA_DB_PATH"],
    )
    canonical_chroma.mkdir()
    (canonical_chroma / "chroma.sqlite3").touch()
    monkeypatch.delenv("ZOTERO_EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_EMBEDDING_MODEL", raising=False)

    snapshot = export_configured_snapshot(config_path)

    assert snapshot["metadata"]["embedding"]["model"] == "gemini-embedding-2-preview"
    assert calls[0][0] == "open"
    assert calls[0][1] != str(canonical_chroma)
    assert calls[0][2] is False
    assert calls[1] == ("get_collection", "zotero_library", None)
    assert calls[2] == ("zotero_port", "23120")
    assert (canonical_chroma / "chroma.sqlite3").read_bytes() == b""
    assert not (tmp_path / "stale-unsupported-path").exists()


def test_configured_model_name_matches_runtime_environment_precedence(monkeypatch):
    config = {
        "embedding_model": "gemini",
        "embedding_config": {"model_name": "configured-gemini"},
    }
    monkeypatch.setenv("ZOTERO_EMBEDDING_MODEL", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "present")
    monkeypatch.setenv("OPENAI_EMBEDDING_MODEL", "environment-openai")

    assert _configured_model_name(config) == "environment-openai"


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        ("default", "all-MiniLM-L6-v2"),
        ("sentence-transformers/custom", "sentence-transformers/custom"),
    ],
)
def test_configured_model_ignores_stale_details_for_default_and_custom(monkeypatch, provider, expected):
    monkeypatch.setenv("ZOTERO_EMBEDDING_MODEL", provider)
    config = {"embedding_config": {"model_name": "stale-detail"}}

    assert _configured_model_name(config) == expected

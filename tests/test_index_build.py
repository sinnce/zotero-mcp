from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest  # pyright: ignore[reportMissingImports]
from chromadb import Documents, EmbeddingFunction, Embeddings  # pyright: ignore[reportMissingImports]

from zotero_mcp.chroma_client import (
    ResolvedEmbedding,
    create_pinned_embedding_function,
    resolve_isolated_embedding,
)
from zotero_mcp.index_build import (
    IsolatedIndexBuildError,
    _rename_no_replace,
    _validate_isolated_path,
    build_isolated_index,
    metadata_document,
    source_identity,
)
from zotero_mcp.snapshot_export import (
    SnapshotCompletenessError,
    SnapshotProvenanceError,
    export_sealed_snapshot,
)


class FakeZotero:
    library_id = "0"
    library_type = "user"
    endpoint = "http://localhost:23120/api"
    local = True

    def __init__(self, items: list[dict[str, Any]], versions: list[int] | None = None) -> None:
        self.items = items
        self.versions = iter(versions or [41] * 10)

    def num_items(self) -> int:
        return len(self.items)

    def top(self, *, start: int, limit: int) -> list[dict[str, Any]]:
        return self.items[start:start + limit]

    def last_modified_version(self) -> int:
        return next(self.versions)


class DeterministicEmbedding(EmbeddingFunction):
    def __init__(self, resolved: ResolvedEmbedding) -> None:  # pyright: ignore[reportMissingSuperCall]
        self.resolved = resolved

    @staticmethod
    def name() -> str:
        return "zotero_mcp_test_3072"

    def get_config(self) -> dict[str, Any]:
        return self.resolved.public_descriptor()

    @staticmethod
    def build_from_config(config: dict[str, Any]) -> DeterministicEmbedding:
        return DeterministicEmbedding(
            ResolvedEmbedding(
                provider=config["provider"],
                route=config["route"],
                model_name=config["model_name"],
                endpoint=config["endpoint"],
                task_type=config["task_type"],
                dimension=config["dimension"],
            )
        )

    def __call__(self, input: Documents) -> Embeddings:
        result = []
        for document in input:
            seed = int(hashlib.sha256(document.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
            result.append([seed + index / 10_000_000 for index in range(3072)])
        return result


class WrongDimensionEmbedding(DeterministicEmbedding):
    @staticmethod
    def name() -> str:
        return "zotero_mcp_test_wrong_dimension"

    def __call__(self, input: Documents) -> Embeddings:
        return [[0.0, 1.0] for _ in input]


class SecretFailingEmbedding(DeterministicEmbedding):
    def __call__(self, input: Documents) -> Embeddings:
        raise RuntimeError("provider request leaked sentinel-secret")


def _resolved() -> ResolvedEmbedding:
    return ResolvedEmbedding(
        provider="gemini",
        route="gemini-direct",
        model_name="gemini-embedding-001",
        endpoint="https://test.invalid/embeddings",
        api_key="must-not-be-persisted",
    )


def _factory(resolved: ResolvedEmbedding) -> DeterministicEmbedding:
    return DeterministicEmbedding(resolved)


def _wrong_factory(resolved: ResolvedEmbedding) -> WrongDimensionEmbedding:
    return WrongDimensionEmbedding(resolved)


def _secret_failing_factory(resolved: ResolvedEmbedding) -> SecretFailingEmbedding:
    return SecretFailingEmbedding(resolved)


def _items() -> list[dict[str, Any]]:
    return [
        {
            "key": "B",
            "data": {
                "key": "B",
                "itemType": "journalArticle",
                "title": "Second",
                "abstractNote": "Abstract B",
                "publicationTitle": "Journal",
                "creators": [{"firstName": "Ada", "lastName": "Lovelace"}],
                "tags": [{"tag": "graph"}],
                "dateModified": "2026-09-08T02:00:00Z",
            },
        },
        {
            "key": "ATTACH",
            "data": {"itemType": "attachment", "dateModified": "2026-09-08T02:00:00Z"},
        },
        {
            "key": "A",
            "data": {
                "key": "A",
                "itemType": "book",
                "title": "First",
                "note": "<b>note</b>",
                "creators": [],
                "dateModified": "2026-09-08T01:00:00Z",
            },
        },
    ]


def _build_for_export(tmp_path: Path) -> tuple[Path, Path]:
    config = tmp_path / "config.json"
    config.write_text("{}\n")
    output = tmp_path / "sealed-index"
    build_isolated_index(FakeZotero(_items()), _resolved(), output, embedding_factory=_factory)
    return config, output


def _rewrite_publication_receipt(output: Path, manifest: dict[str, object]) -> None:
    publication_path = output / "publication.json"
    publication = json.loads(publication_path.read_text())
    payload = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    publication["manifest_sha256"] = hashlib.sha256(payload).hexdigest()
    publication["index_identity"] = manifest["index_identity"]
    publication_path.write_text(json.dumps(publication))


def test_builds_real_temporary_chroma_and_seals_after_verification(tmp_path: Path) -> None:
    protected_config = tmp_path / "config.json"
    protected_config.write_text('{"protected":true}\n')
    protected_index = tmp_path / "old-index"
    protected_index.mkdir()
    (protected_index / "sentinel").write_bytes(b"unchanged")
    output = tmp_path / "fresh-index"

    manifest = cast(
        dict[str, Any],
        build_isolated_index(
            FakeZotero(_items()),
            _resolved(),
            output,
            protected_paths=(protected_config, protected_index),
            embedding_factory=_factory,
        ),
    )

    assert manifest["status"] == "complete"
    assert manifest["counts"] == {"expected": 2, "indexed": 2}
    assert manifest["embedding"] == _resolved().public_descriptor()
    assert set(manifest["producer"]["dependencies"]) == {"chromadb", "google-genai"}
    assert all(manifest["producer"]["dependencies"].values())
    assert manifest["collection_metadata"]["embedding_route"] == "gemini-direct"
    assert (output / "manifest.json").is_file()
    assert json.loads((output / "manifest.json").read_text()) == manifest
    assert protected_config.read_text() == '{"protected":true}\n'
    assert (protected_index / "sentinel").read_bytes() == b"unchanged"
    assert not list(tmp_path.glob(".fresh-index.*.json"))
    assert not list(output.glob(".manifest*"))
    run_state_path = tmp_path / manifest["coordinator_run_state"]["file"]
    run_state = json.loads(run_state_path.read_text())
    assert run_state["status"] == "complete"
    assert run_state["source_scope_hash"] == manifest["source_scope_hash"]
    assert run_state["manifest_sha256"] == json.loads((output / "publication.json").read_text())["manifest_sha256"]
    persisted_text = b"".join(path.read_bytes() for path in output.rglob("*") if path.is_file())
    assert b"must-not-be-persisted" not in persisted_text


def test_direct_build_descriptor_sanitizes_endpoint_before_persistence(tmp_path: Path) -> None:
    resolved = ResolvedEmbedding(
        provider="gemini",
        route="gemini-direct",
        model_name="gemini-embedding-001",
        endpoint="https://example.invalid/v1beta?token=sentinel-secret",
        api_key="test-key",
    )
    output = tmp_path / "fresh-index"

    manifest = cast(
        dict[str, Any],
        build_isolated_index(FakeZotero(_items()), resolved, output, embedding_factory=_factory),
    )

    assert manifest["embedding"]["endpoint"] == "https://example.invalid/v1beta"
    assert b"sentinel-secret" not in b"".join(path.read_bytes() for path in output.rglob("*") if path.is_file())


def test_direct_descriptor_rejects_endpoint_userinfo() -> None:
    with pytest.raises(ValueError, match="must not contain credentials"):
        ResolvedEmbedding(
            provider="gemini",
            route="gemini-direct",
            model_name="gemini-embedding-001",
            endpoint="https://user:sentinel-secret@example.invalid/v1beta",
            api_key="test-key",
        )


def test_production_factory_round_trips_complete_descriptor(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        pass

    monkeypatch.setattr("google.genai.Client", lambda **kwargs: FakeClient())
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    embedding = create_pinned_embedding_function(_resolved())

    assert embedding.get_config() == _resolved().public_descriptor()
    rebuilt = type(embedding).build_from_config(embedding.get_config())
    assert rebuilt.get_config() == _resolved().public_descriptor()


def test_production_embedding_retries_same_pinned_request(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, list[str], object]] = []

    class Models:
        def embed_content(self, *, model: str, contents: list[str], config: object) -> object:
            calls.append((model, contents, config))
            if len(calls) < 3:
                raise RuntimeError("503 UNAVAILABLE")
            return SimpleNamespace(embeddings=[SimpleNamespace(values=[0.0] * 3072)])

    monkeypatch.setattr("google.genai.Client", lambda **kwargs: SimpleNamespace(models=Models()))
    sleeps: list[int] = []
    monkeypatch.setattr("zotero_mcp.chroma_client.time.sleep", sleeps.append)
    embedding = create_pinned_embedding_function(_resolved())

    vectors = embedding(["document"])
    assert list(vectors[0]) == [0.0] * 3072
    assert [(model, contents) for model, contents, _ in calls] == [
        (_resolved().model_name, ["document"]),
        (_resolved().model_name, ["document"]),
        (_resolved().model_name, ["document"]),
    ]
    assert sleeps == [1, 2]


def test_failed_vector_validation_never_publishes_or_seals(tmp_path: Path) -> None:
    output = tmp_path / "fresh-index"
    with pytest.raises(IsolatedIndexBuildError, match="writer subprocess failed"):
        build_isolated_index(
            FakeZotero(_items()),
            _resolved(),
            output,
            embedding_factory=_wrong_factory,
        )

    assert not output.exists()
    staging = list(tmp_path.glob("fresh-index.building.*"))
    assert len(staging) == 1
    assert not (staging[0] / "manifest.json").exists()
    assert json.loads((staging[0] / "failure.json").read_text())["status"] == "failed"


def test_child_failure_never_prints_provider_exception_text(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(IsolatedIndexBuildError, match="writer subprocess failed"):
        build_isolated_index(
            FakeZotero(_items()),
            _resolved(),
            tmp_path / "fresh-index",
            embedding_factory=_secret_failing_factory,
        )

    captured = capfd.readouterr()
    assert "sentinel-secret" not in captured.out
    assert "sentinel-secret" not in captured.err


def test_publish_race_invalidates_staging_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def lose_race(source: Path, destination: Path) -> None:
        destination.mkdir()
        raise IsolatedIndexBuildError("atomic no-replace publication failed: File exists")

    monkeypatch.setattr("zotero_mcp.index_build._rename_no_replace", lose_race)
    output = tmp_path / "fresh-index"
    with pytest.raises(IsolatedIndexBuildError, match="File exists"):
        build_isolated_index(FakeZotero(_items()), _resolved(), output, embedding_factory=_factory)

    staging = next(tmp_path.glob("fresh-index.building.*"))
    assert not (staging / "manifest.json").exists()
    assert json.loads((staging / "failure.json").read_text())["status"] == "failed"


def test_interrupt_after_manifest_creation_invalidates_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def interrupt(source: Path, destination: Path) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("zotero_mcp.index_build._rename_no_replace", interrupt)
    output = tmp_path / "fresh-index"
    with pytest.raises(KeyboardInterrupt):
        build_isolated_index(FakeZotero(_items()), _resolved(), output, embedding_factory=_factory)

    staging = next(tmp_path.glob("fresh-index.building.*"))
    assert not (staging / "manifest.json").exists()
    assert not (staging / "publication.json").exists()
    assert json.loads((staging / "failure.json").read_text())["error_type"] == "KeyboardInterrupt"
    run_state = json.loads(next(tmp_path.glob("fresh-index.run.*.json")).read_text())
    assert run_state["status"] == "failed"
    assert run_state["source_scope_hash"]


def test_parent_fsync_failure_after_rename_invalidates_published_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from zotero_mcp import index_build

    output = tmp_path / "fresh-index"
    original_fsync = index_build._fsync_directory

    def fail_after_rename(path: Path) -> None:
        if path == output.parent and output.exists():
            raise OSError("injected parent fsync failure")
        original_fsync(path)

    monkeypatch.setattr(index_build, "_fsync_directory", fail_after_rename)
    with pytest.raises(OSError, match="injected parent fsync failure"):
        build_isolated_index(FakeZotero(_items()), _resolved(), output, embedding_factory=_factory)

    assert output.is_dir()
    assert not (output / "manifest.json").exists()
    assert not (output / "publication.json").exists()
    assert json.loads((output / "failure.json").read_text())["status"] == "failed"
    config = tmp_path / "config.json"
    config.write_text("{}\n")
    with pytest.raises(SnapshotCompletenessError, match="manifest is unavailable"):
        export_sealed_snapshot(config, output)


def test_source_change_prevents_publication(tmp_path: Path) -> None:
    with pytest.raises(IsolatedIndexBuildError, match="library changed"):
        build_isolated_index(
            FakeZotero(_items(), versions=[41, 41, 42]),
            _resolved(),
            tmp_path / "fresh-index",
            embedding_factory=_factory,
        )
    assert not (tmp_path / "fresh-index").exists()
    staging = next(tmp_path.glob("fresh-index.building.*"))
    assert json.loads((staging / "failure.json").read_text())["phase"] == "build-verify-seal"


def test_output_overlap_and_existing_empty_destination_fail_closed(tmp_path: Path) -> None:
    protected = tmp_path / "protected"
    protected.mkdir()
    (protected / "sentinel").write_text("safe")
    client = FakeZotero(_items())
    with pytest.raises(IsolatedIndexBuildError, match="overlaps protected"):
        build_isolated_index(client, _resolved(), protected / "child", protected_paths=(protected,))

    source = tmp_path / "source"
    source.mkdir()
    (source / "payload").write_text("new")
    destination = tmp_path / "destination"
    destination.mkdir()
    (destination / "sentinel").write_text("old")
    with pytest.raises(IsolatedIndexBuildError, match="no-replace publication failed"):
        _rename_no_replace(source, destination)
    assert (source / "payload").read_text() == "new"
    assert (destination / "sentinel").read_text() == "old"


def test_output_overlap_rejects_children_and_parents_of_real_and_dev_indexes(tmp_path: Path) -> None:
    real_index = tmp_path / "real" / "chroma_db"
    dev_index = tmp_path / "dev" / "chroma_db"
    with pytest.raises(IsolatedIndexBuildError, match="overlaps protected"):
        _validate_isolated_path(real_index / "candidate", (real_index, dev_index))
    with pytest.raises(IsolatedIndexBuildError, match="overlaps protected"):
        _validate_isolated_path(dev_index.parent, (real_index, dev_index))


def test_source_identity_includes_port_path_and_policies() -> None:
    identity = source_identity(FakeZotero(_items()))
    assert identity["endpoint"] == {
        "scheme": "http",
        "host": "localhost",
        "port": 23120,
        "path": "/api",
    }
    assert identity["source_kind"] == "local-api"
    assert identity["library_id"] == "0"
    assert identity["text_policy"] == "zotero-mcp-metadata-text/1.0.0"


def test_metadata_document_is_exact_metadata_only_text() -> None:
    assert metadata_document(_items()[0]) == "Second Lovelace, Ada Abstract B Journal graph"
    assert metadata_document(_items()[2]) == "First No authors listed note"


def test_conflicting_item_keys_fail_before_embedding(tmp_path: Path) -> None:
    item = _items()[0]
    item["data"]["key"] = "DIFFERENT"
    with pytest.raises(IsolatedIndexBuildError, match="conflicting top-level and data keys"):
        build_isolated_index(FakeZotero([item]), _resolved(), tmp_path / "fresh-index", embedding_factory=_factory)
    assert not (tmp_path / "fresh-index").exists()


def test_resolver_pins_direct_route_and_redacts_credentials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "semantic_search": {
                    "embedding_model": "gemini",
                    "embedding_config": {"model_name": "gemini-embedding-001"},
                }
            }
        )
    )
    monkeypatch.setenv("GEMINI_API_KEY", "top-secret")
    monkeypatch.setenv("GEMINI_BASE_URL", "https://example.invalid/v1beta?secret=value")
    monkeypatch.setenv("OPENROUTER_API_KEY", "fallback-secret")

    resolved = resolve_isolated_embedding(str(config))

    assert resolved.route == "gemini-direct"
    assert resolved.dimension == 3072
    assert resolved.task_type == "RETRIEVAL_DOCUMENT"
    assert resolved.endpoint == "https://example.invalid/v1beta"
    assert "secret" not in json.dumps(resolved.public_descriptor())
    assert "top-secret" not in repr(resolved)
    assert resolved.api_key == "top-secret"
    assert resolved.public_descriptor()["producer"] == "zotero-mcp"
    assert resolved.public_descriptor()["descriptor_schema_version"] == "zotero-mcp-embedding-descriptor/1.0.0"


def test_resolver_rejects_openrouter_for_isolated_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "semantic_search": {
                    "embedding_model": "gemini",
                    "embedding_config": {"model_name": "gemini-embedding-001", "isolated_route": "openrouter"},
                }
            }
        )
    )
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "must-not-be-used")

    with pytest.raises(ValueError, match="only the gemini-direct"):
        resolve_isolated_embedding(str(config))


def test_resolver_rejects_credentials_embedded_in_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "semantic_search": {
                    "embedding_model": "gemini",
                        "embedding_config": {
                            "model_name": "gemini-embedding-001",
                            "api_key": "test-key",
                            "base_url": "https://user:secret@example.invalid/v1beta",
                    },
                }
            }
        )
    )
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    with pytest.raises(ValueError, match="must not contain credentials"):
        resolve_isolated_embedding(str(config))


def test_build_cli_applies_explicit_source_before_client_and_protects_real_and_dev_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from zotero_mcp import cli

    config_home = tmp_path / "config-home"
    real_config = config_home / "zotero-mcp" / "config.json"
    real_config.parent.mkdir(parents=True)
    real_config.write_text(
        json.dumps(
            {
                "client_env": {
                    "ZOTERO_LOCAL": "true",
                    "ZOTERO_LOCAL_PORT": "23119",
                    "ZOTERO_LIBRARY_ID": "9",
                    "ZOTERO_LIBRARY_TYPE": "group",
                }
            }
        )
    )
    explicit_config = tmp_path / "explicit-dev" / "config.json"
    explicit_config.parent.mkdir()
    explicit_config.write_text(
        json.dumps(
            {
                "client_env": {
                    "ZOTERO_LOCAL": "true",
                    "ZOTERO_LOCAL_PORT": "23120",
                    "ZOTERO_LIBRARY_ID": "0",
                    "ZOTERO_LIBRARY_TYPE": "user",
                },
                "semantic_search": {
                    "embedding_model": "gemini",
                    "embedding_config": {
                        "model_name": "gemini-embedding-001",
                        "api_key": "test-key",
                    },
                },
            }
        )
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setenv("ZOTERO_MCP_CONFIG_PATH", str(real_config))
    monkeypatch.setenv("ZOTERO_LOCAL", "true")
    monkeypatch.setenv("ZOTERO_LOCAL_PORT", "23119")
    monkeypatch.setenv("ZOTERO_LIBRARY_ID", "9")
    monkeypatch.setenv("ZOTERO_LIBRARY_TYPE", "group")
    monkeypatch.setenv("ZOTERO_API_KEY", "ambient-real-secret")

    def client() -> FakeZotero:
        assert os.environ["ZOTERO_LOCAL_PORT"] == "23120"
        assert os.environ["ZOTERO_LIBRARY_ID"] == "0"
        assert os.environ["ZOTERO_LIBRARY_TYPE"] == "user"
        assert "ZOTERO_API_KEY" not in os.environ
        return FakeZotero(_items())

    captured: dict[str, object] = {}

    def build(*args: object, **kwargs: object) -> dict[str, object]:
        captured["protected_paths"] = kwargs["protected_paths"]
        return {
            "index_identity": {"index_uuid": "index", "build_run_id": "run"},
            "counts": {"expected": 2, "indexed": 2},
        }

    monkeypatch.setattr("zotero_mcp.client.get_zotero_client", client)
    monkeypatch.setattr("zotero_mcp.index_build.build_isolated_index", build)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "zotero-mcp",
            "build-index",
            "--config-path",
            str(explicit_config),
            "--output",
            str(tmp_path / "fresh-index"),
        ],
    )

    cli.main()

    protected = set(cast(tuple[Path, ...], captured["protected_paths"]))
    assert real_config.resolve() in protected
    assert (config_home / "zotero-mcp" / "chroma_db").resolve() in protected
    assert (config_home / "zotero-mcp-dev" / "config.json").resolve() in protected
    assert (config_home / "zotero-mcp-dev" / "chroma_db").resolve() in protected
    assert explicit_config.resolve() in protected
    assert (explicit_config.parent / "chroma_db").resolve() in protected


def test_strict_source_replaces_ambient_web_api_credential(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from zotero_mcp.cli import setup_zotero_environment

    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "client_env": {
                    "ZOTERO_LOCAL": "false",
                    "ZOTERO_LOCAL_PORT": "23119",
                    "ZOTERO_LIBRARY_ID": "123",
                    "ZOTERO_LIBRARY_TYPE": "user",
                    "ZOTERO_API_KEY": "explicit-web-key",
                }
            }
        )
    )
    monkeypatch.setenv("ZOTERO_API_KEY", "ambient-web-key")

    setup_zotero_environment(config, strict_source=True)

    assert os.environ["ZOTERO_LOCAL"] == "false"
    assert os.environ["ZOTERO_API_KEY"] == "explicit-web-key"


def test_strict_source_rejects_invalid_local_mode(tmp_path: Path) -> None:
    from zotero_mcp.cli import setup_zotero_environment

    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "client_env": {
                    "ZOTERO_LOCAL": "maybe",
                    "ZOTERO_LOCAL_PORT": "23120",
                    "ZOTERO_LIBRARY_ID": "0",
                    "ZOTERO_LIBRARY_TYPE": "user",
                }
            }
        )
    )

    with pytest.raises(ValueError, match="must be exactly true or false"):
        setup_zotero_environment(config, strict_source=True)


def test_wrong_dimension_is_rejected_before_staging_is_created(tmp_path: Path) -> None:
    invalid = ResolvedEmbedding(
        provider="gemini",
        route="gemini-direct",
        model_name="gemini-embedding-001",
        endpoint="https://test.invalid/embeddings",
        dimension=2,
    )
    with pytest.raises(IsolatedIndexBuildError, match="dimension 3072"):
        build_isolated_index(FakeZotero(_items()), invalid, tmp_path / "fresh-index", embedding_factory=_factory)
    assert list(tmp_path.iterdir()) == []


def test_sealed_export_accepts_matching_source_and_index_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, output = _build_for_export(tmp_path)
    monkeypatch.setattr("zotero_mcp.client.get_zotero_client", lambda: FakeZotero(_items()))

    snapshot = cast(dict[str, Any], export_sealed_snapshot(config, output))

    assert len(snapshot["papers"]) == 2
    assert len(snapshot["embeddings"]) == 2
    manifest = json.loads((output / "manifest.json").read_text())
    assert snapshot["metadata"]["sealed_build"] == {
        "manifest_schema_version": manifest["schema_version"],
        "producer": manifest["producer"],
        "coordinator_run_state": manifest["coordinator_run_state"],
        "source_identity": manifest["source_identity"],
        "zotero_library_version": manifest["zotero_library_version"],
        "source_scope_hash": manifest["source_scope_hash"],
        "index_identity": manifest["index_identity"],
        "embedding": manifest["embedding"],
        "embedding_descriptor_hash": manifest["embedding_descriptor_hash"],
        "collection_metadata": manifest["collection_metadata"],
        "collection_metadata_hash": manifest["collection_metadata_hash"],
        "content_hashes": manifest["hashes"],
        "counts": manifest["counts"],
    }


def test_sealed_export_requires_publication_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, output = _build_for_export(tmp_path)
    (output / "publication.json").unlink()
    monkeypatch.setattr("zotero_mcp.client.get_zotero_client", lambda: FakeZotero(_items()))

    with pytest.raises(SnapshotCompletenessError, match="publication receipt is unavailable"):
        export_sealed_snapshot(config, output)


@pytest.mark.parametrize("target", ["config", "manifest", "chroma", "nested"])
def test_export_cli_rejects_protected_output_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    from zotero_mcp import cli

    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "client_env": {
                    "ZOTERO_LOCAL": "true",
                    "ZOTERO_LOCAL_PORT": "23120",
                    "ZOTERO_LIBRARY_ID": "0",
                    "ZOTERO_LIBRARY_TYPE": "user",
                }
            }
        )
    )
    sealed = tmp_path / "sealed"
    sealed.mkdir()
    manifest = sealed / "manifest.json"
    chroma = sealed / "chroma.sqlite3"
    nested = sealed / "exports" / "snapshot.json"
    manifest.write_bytes(b"manifest-sentinel")
    chroma.write_bytes(b"chroma-sentinel")
    before = {path: path.read_bytes() for path in (config, manifest, chroma)}
    targets = {"config": config, "manifest": manifest, "chroma": chroma, "nested": nested}
    called = False

    def must_not_export(*args: object, **kwargs: object) -> dict[str, object]:
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr("zotero_mcp.snapshot_export.export_sealed_snapshot", must_not_export)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "zotero-mcp",
            "export-snapshot",
            "--config-path",
            str(config),
            "--index-path",
            str(sealed),
            "--output",
            str(targets[target]),
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 2
    assert called is False
    assert {path: path.read_bytes() for path in before} == before


def test_sealed_export_rejects_same_library_on_different_api_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, output = _build_for_export(tmp_path)
    client = FakeZotero(_items())
    client.endpoint = "http://localhost:23119/api"
    monkeypatch.setattr("zotero_mcp.client.get_zotero_client", lambda: client)

    with pytest.raises(SnapshotProvenanceError, match="source identity differs"):
        export_sealed_snapshot(config, output)


def test_sealed_export_binds_manifest_source_to_collection_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, output = _build_for_export(tmp_path)
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["source_identity"]["endpoint"]["port"] = 23119
    manifest_path.write_text(json.dumps(manifest))
    _rewrite_publication_receipt(output, manifest)
    client = FakeZotero(_items())
    client.endpoint = "http://localhost:23119/api"
    monkeypatch.setattr("zotero_mcp.client.get_zotero_client", lambda: client)

    with pytest.raises(SnapshotProvenanceError, match="source identity hash"):
        export_sealed_snapshot(config, output)


def test_sealed_export_binds_descriptor_hash_to_collection_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, output = _build_for_export(tmp_path)
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["collection_metadata"]["embedding_descriptor_hash"] = "0" * 64
    payload = json.dumps(
        manifest["collection_metadata"], ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    manifest["collection_metadata_hash"] = hashlib.sha256(payload).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    _rewrite_publication_receipt(output, manifest)
    monkeypatch.setattr("zotero_mcp.client.get_zotero_client", lambda: FakeZotero(_items()))

    with pytest.raises(SnapshotProvenanceError, match="embedding descriptor hash"):
        export_sealed_snapshot(config, output)


def test_sealed_export_binds_dependency_versions_to_collection_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, output = _build_for_export(tmp_path)
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["producer"]["dependencies"]["chromadb"] = "altered"
    manifest_path.write_text(json.dumps(manifest))
    _rewrite_publication_receipt(output, manifest)
    monkeypatch.setattr("zotero_mcp.client.get_zotero_client", lambda: FakeZotero(_items()))

    with pytest.raises(SnapshotProvenanceError, match="dependency versions differ"):
        export_sealed_snapshot(config, output)


def test_sealed_export_rejects_replaced_collection_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, output = _build_for_export(tmp_path)
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["index_identity"]["collection_id"] = "00000000-0000-0000-0000-000000000000"
    manifest_path.write_text(json.dumps(manifest))
    _rewrite_publication_receipt(output, manifest)
    monkeypatch.setattr("zotero_mcp.client.get_zotero_client", lambda: FakeZotero(_items()))

    with pytest.raises(SnapshotProvenanceError, match="collection ID differs"):
        export_sealed_snapshot(config, output)


def test_sealed_export_rejects_altered_build_run_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, output = _build_for_export(tmp_path)
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["collection_metadata"]["build_run_id"] = "altered-run"
    payload = json.dumps(
        manifest["collection_metadata"], ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    manifest["collection_metadata_hash"] = hashlib.sha256(payload).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    _rewrite_publication_receipt(output, manifest)
    monkeypatch.setattr("zotero_mcp.client.get_zotero_client", lambda: FakeZotero(_items()))

    with pytest.raises(SnapshotProvenanceError, match="collection metadata differs"):
        export_sealed_snapshot(config, output)


def test_sealed_export_recomputes_manifest_content_hashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, output = _build_for_export(tmp_path)
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["hashes"]["vectors_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest))
    _rewrite_publication_receipt(output, manifest)
    monkeypatch.setattr("zotero_mcp.client.get_zotero_client", lambda: FakeZotero(_items()))

    with pytest.raises(SnapshotProvenanceError, match="contents differ"):
        export_sealed_snapshot(config, output)

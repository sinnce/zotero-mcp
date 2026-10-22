"""Offline contract and real search-path tests using synthetic vectors only."""

import json
import socket
from unittest.mock import MagicMock, patch

import chromadb
import numpy as np
import pytest
from chromadb.config import Settings

from zotero_mcp import retrieval_eval as ev
from zotero_mcp.chroma_client import ChromaClient


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("Tests must not access the network")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket.socket, "connect_ex", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setenv("ANONYMIZED_TELEMETRY", "False")


def suite_file(tmp_path, rows=None):
    path = tmp_path / "suite.jsonl"
    rows = rows or [
        dict(query_id="q1", text="first", exclude_ids=[], candidate_ids=None),
        dict(query_id="q2", text="second", exclude_ids=["A"], candidate_ids=["A", "B"]),
        dict(query_id="q3", text="first", exclude_ids=[], candidate_ids=[]),
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return path


def filled_cache(path, dimension=3):
    cache = ev.QueryCache(path, dimension=dimension)
    for text, vector in [("first", [1, 0, 0]), ("second", [0, 1, 0])]:
        cache.vectors[ev.text_key(text)] = np.asarray(vector, dtype=np.float32)
        cache.metadata["entries"].append(dict(key=ev.text_key(text), text=text))
    cache.save()
    return cache


def test_cache_format_hit_miss(tmp_path):
    filled_cache(tmp_path)
    cache = ev.QueryCache(tmp_path, dimension=3)
    assert cache.embed_query("first") == [1, 0, 0]
    with pytest.raises(ValueError, match="cache miss"):
        cache.embed_query(" first")
    with np.load(tmp_path / "query-vectors.npz", allow_pickle=False) as data:
        assert set(data.files) == {"keys", "vectors"}
        assert data["keys"].dtype.kind == "U"
        assert data["vectors"].dtype == np.float32
        assert data["vectors"].shape == (2, 3)
    with pytest.raises(ValueError, match="model mismatch"):
        ev.QueryCache(tmp_path, model="other", dimension=3)
    with pytest.raises(ValueError, match="dimension mismatch"):
        ev.QueryCache(tmp_path)


def test_fill_only_misses_and_production_function(tmp_path, monkeypatch):
    suite = suite_file(tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "test-secret")
    # Mock only the provider transport; exercise the production embed_query implementation.
    with patch("google.genai.Client") as client:
        from google.genai import types

        response = types.EmbedContentResponse(
            embeddings=[
                types.ContentEmbedding(values=[1, 0, 0], statistics=types.ContentEmbeddingStatistics(token_count=3))
            ],
            metadata=types.EmbedContentMetadata(billable_character_count=5),
        )
        client.return_value.models.embed_content.return_value = response
        ev.embed_queries(suite, tmp_path / "cache", dimension=3)
        ev.embed_queries(suite, tmp_path / "cache", dimension=3)
        assert client.call_args.kwargs["http_options"].retry_options.attempts == 1
        assert client.call_args.kwargs["http_options"].base_url == "https://generativelanguage.googleapis.com"
        assert client.return_value.models.embed_content.call_count == 2
        for call in client.return_value.models.embed_content.call_args_list:
            assert call.kwargs["config"].task_type.lower() == "retrieval_query"
            assert call.kwargs["config"].output_dimensionality is None
            assert call.kwargs["contents"] in [["first"], ["second"]]
        metadata = json.loads((tmp_path / "cache" / "query-vectors.json").read_text())
        assert metadata["usage"]["responses"][0]["statistics"]["token_count"] == 3
        assert metadata["usage"]["responses"][0]["request"]["billable_character_count"] == 5
        assert "test-secret" not in (tmp_path / "cache" / "query-vectors.json").read_text()


def test_cost_cap_before_provider(tmp_path):
    suite = suite_file(tmp_path)
    with patch.object(ev, "GeminiEmbeddingFunction") as provider:
        with pytest.raises(ValueError, match="exceeds cap"):
            ev.embed_queries(suite, tmp_path / "cache", max_cost_usd=0)
        provider.assert_not_called()


def test_retries_charge_budget_and_resume(tmp_path, monkeypatch):
    suite = suite_file(tmp_path, [dict(query_id="q", text="x")])
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setattr(ev.time, "sleep", lambda _: None)
    with patch.object(ev, "GeminiEmbeddingFunction") as provider:
        provider.return_value.embed_query.side_effect = RuntimeError("do not disclose provider payload")
        with pytest.raises(ValueError, match="including retries"):
            ev.embed_queries(suite, tmp_path / "cache", dimension=3, max_cost_usd=0.000007)
        assert provider.return_value.embed_query.call_count == 1
    cache = ev.QueryCache(tmp_path / "cache", dimension=3)
    assert cache.metadata["usage"]["attempts"] == 1
    assert cache.metadata["est_cost_usd"] > 0


def test_wrong_returned_dimension(tmp_path, monkeypatch):
    suite = suite_file(tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    with patch.object(ev, "GeminiEmbeddingFunction") as provider:
        provider.return_value.embed_query.return_value = [1, 0]
        with pytest.raises(ValueError, match="dimension"):
            ev.embed_queries(suite, tmp_path / "cache", dimension=3)


@pytest.mark.parametrize("missing", [False, True])
def test_attach_never_constructs_creates_or_deletes(missing, tmp_path):
    (tmp_path / "chroma.sqlite3").touch()
    with (
        patch.object(ChromaClient, "__init__", side_effect=AssertionError("constructor")),
        patch("zotero_mcp.chroma_client.chromadb.PersistentClient") as persistent,
    ):
        backend = persistent.return_value
        if missing:
            backend.get_collection.side_effect = ValueError("missing collection")
            with pytest.raises(ValueError, match="missing"):
                ChromaClient.attach_existing(str(tmp_path))
        else:
            attached = ChromaClient.attach_existing(str(tmp_path))
            assert attached.collection is backend.get_collection.return_value
        backend.get_collection.assert_called_once_with(name="zotero_library", embedding_function=None)
        backend.delete_collection.assert_not_called()
        backend.create_collection.assert_not_called()
        backend.get_or_create_collection.assert_not_called()


def test_attach_missing_store_does_not_create(tmp_path):
    path = tmp_path / "absent"
    with patch("zotero_mcp.chroma_client.chromadb.PersistentClient") as persistent:
        with pytest.raises(FileNotFoundError, match="existing persistent"):
            ChromaClient.attach_existing(str(path))
        persistent.assert_not_called()
    assert not path.exists()


def test_injected_embedder_overrides_dispatch():
    client = ChromaClient.__new__(ChromaClient)
    client.embedding_function = MagicMock()
    client.collection = MagicMock()
    client.collection.query.return_value = {"ids": [[]]}
    embedder = MagicMock(return_value=[1, 0])
    client.search(["test"], query_embedder=embedder)
    embedder.assert_called_once_with("test")
    assert client.collection.query.call_args.kwargs["query_embeddings"] == [[1, 0]]
    assert "query_texts" not in client.collection.query.call_args.kwargs
    client.embedding_function.embed_query.assert_not_called()


def synthetic_snapshot(tmp_path):
    snapshot = tmp_path / "snapshot"
    path = snapshot / "chroma_db"
    backend = chromadb.PersistentClient(path=str(path), settings=Settings(anonymized_telemetry=False))
    collection = backend.create_collection("zotero_library", embedding_function=None)
    collection.add(
        ids=["A", "B", "C"],
        embeddings=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
        documents=["Alpha", "Beta", "Gamma"],
        metadatas=[dict(item_key=key, title=key) for key in "ABC"],
    )
    return snapshot


def test_real_path_rankings_and_source_hashes(tmp_path, monkeypatch):
    snapshot = synthetic_snapshot(tmp_path)
    for path in [snapshot, *snapshot.rglob("*")]:
        path.chmod(0o555 if path.is_dir() else 0o444)
    suite = suite_file(tmp_path)
    cache_dir = tmp_path / "cache"
    filled_cache(cache_dir)
    original_cache = ev.QueryCache
    monkeypatch.setattr(ev, "QueryCache", lambda path: original_cache(path, dimension=3))
    monkeypatch.setattr(ev, "EXPECTED_COUNT", 3)
    before = ev.hash_tree(snapshot)
    output = tmp_path / "output"
    with (
        patch("zotero_mcp.semantic_search.get_zotero_client", side_effect=AssertionError("live Zotero")),
        patch("zotero_mcp.chroma_client.GeminiEmbeddingFunction.embed_query", side_effect=AssertionError("provider")),
    ):
        ev.run(snapshot, suite, cache_dir, "baseline-dense", output, k=3)
    rows = [json.loads(line) for line in (output / "rankings.jsonl").read_text().splitlines()]
    assert [row["query_id"] for row in rows] == ["q1", "q2", "q3"]
    assert rows[2]["ranking"] == []
    assert rows[0]["ranking"][0]["item_key"] == "A"
    assert rows[1]["ranking"] == [{"item_key": "B", "score": 1.0}]
    for row in rows:
        assert set(row) == {"query_id", "system", "ranking"}
        assert row["system"] == "zmcp-baseline-dense"
        keys = [r["item_key"] for r in row["ranking"]]
        assert len(keys) == len(set(keys))
        assert all(set(r) == {"item_key", "score"} for r in row["ranking"])
    manifest = json.loads((output / "rankings.manifest.json").read_text())
    assert manifest["snapshot_hashes"] == manifest["snapshot_hashes_after"] == before == ev.hash_tree(snapshot)
    assert manifest["ann_vs_exact_overlap_at_10"] == 1.0
    assert manifest["collection_count"] == 3
    assert (snapshot / "chroma_db" / "chroma.sqlite3").stat().st_mode & 0o222 == 0
    assert (output / "scratch" / "chroma_db" / "chroma.sqlite3").stat().st_mode & 0o200
    with pytest.raises(FileExistsError):
        ev.run(snapshot, suite, cache_dir, "baseline-dense", output)


def test_run_refuses_cache_miss_before_output(tmp_path, monkeypatch):
    suite = suite_file(tmp_path, [dict(query_id="missing", text="uncached")])
    filled_cache(tmp_path / "cache")
    original = ev.QueryCache
    monkeypatch.setattr(ev, "QueryCache", lambda path: original(path, dimension=3))
    with patch.object(ChromaClient, "attach_existing", side_effect=AssertionError("must not open")):
        with pytest.raises(ValueError, match="cache miss"):
            ev.run(tmp_path / "source", suite, tmp_path / "cache", "baseline-dense", tmp_path / "output")
    assert not (tmp_path / "output").exists()


def test_missing_gemini_env_does_not_use_google_key(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "must-not-use")
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        ev.embed_queries(suite_file(tmp_path), tmp_path / "cache")


def test_count_mismatch_preserves_source(tmp_path, monkeypatch):
    snapshot = synthetic_snapshot(tmp_path)
    suite = suite_file(tmp_path)
    filled_cache(tmp_path / "cache")
    original = ev.QueryCache
    monkeypatch.setattr(ev, "QueryCache", lambda path: original(path, dimension=3))
    before = ev.hash_tree(snapshot)
    with pytest.raises(ValueError, match="Expected 3309"):
        ev.run(snapshot, suite, tmp_path / "cache", "baseline-dense", tmp_path / "output")
    assert ev.hash_tree(snapshot) == before
    assert not (tmp_path / "output" / "rankings.manifest.json").exists()


def test_cache_checksum_rejects_tampering(tmp_path):
    filled_cache(tmp_path)
    meta = tmp_path / "query-vectors.json"
    data = json.loads(meta.read_text())
    data["entries"][0]["text"] = "tampered"
    meta.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="checksum"):
        ev.QueryCache(tmp_path, dimension=3)


def test_network_denial():
    with ev.offline(), pytest.raises(RuntimeError, match="disabled"):
        socket.create_connection(("example.invalid", 443))


def test_injected_search_preserves_mcp_markdown(tmp_path, monkeypatch, dummy_ctx):
    from zotero_mcp import semantic_search
    from zotero_mcp.tools import search as tool

    client = ChromaClient.__new__(ChromaClient)
    client.embedding_function = None
    client.query_embedder = lambda text: [1, 0, 0]
    client.collection = MagicMock()
    client.collection.query.return_value = dict(
        ids=[["A"]], distances=[[0.0]], documents=[["Alpha text"]], metadatas=[[{"title": "Alpha"}]]
    )
    zotero = ev.SnapshotZotero(dict(ids=["A"], metadatas=[{"title": "Alpha"}]))
    with patch.object(semantic_search, "get_zotero_client", side_effect=AssertionError("live client")):
        search = semantic_search.ZoteroSemanticSearch(chroma_client=client, zotero_client=zotero)
    monkeypatch.setattr(tool, "get_config_path", lambda: tmp_path / "unused.json")
    monkeypatch.setattr(semantic_search, "create_semantic_search", lambda _: search)
    result = tool.semantic_search(query="first", limit=1, ctx=dummy_ctx)
    assert result.startswith("# Semantic Search Results for 'first'")
    assert "Alpha" in result
    assert "1.000" in result
    assert "A" in result

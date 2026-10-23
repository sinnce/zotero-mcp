"""Synthetic, offline retrieval behavior and compatibility checks."""
from unittest.mock import MagicMock

import chromadb
import pytest
from chromadb.config import Settings

from zotero_mcp.chroma_client import ChromaClient
from zotero_mcp.retrieval import (
    LexicalIndex,
    collapse_results,
    fuse,
    is_web_clip,
    local_similarity,
    matches_filter,
    select_results,
    supports_filter,
)
from zotero_mcp.retrieval_eval import profile_config
from zotero_mcp.semantic_search import ZoteroSemanticSearch


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import socket

    def deny(*args, **kwargs):
        raise AssertionError("Hybrid tests must stay offline")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)


def record(key, title="", doi=""):
    return dict(item_key=key, metadata=dict(title=title, DOI=doi), matched_text=title, similarity_score=0.7)


@pytest.mark.parametrize("query", ['검색 강화학습', '"alpha" OR NOT NEAR * : (beta)', 'alpha-beta?!', '""*:( )', 'title:alpha'])
def test_fts_escape(query):
    index = LexicalIndex([(str(i), title, {"title": title}) for i, title in enumerate(["검색 강화학습", "alpha beta", "OR NOT NEAR"])])
    results = index.search(query)
    assert isinstance(results, list)
    if query == '검색 강화학습':
        assert results[0]["item_key"] == "0"
    if query == '""*:( )':
        assert results == []


def test_rrf_math_gate_and_minimum():
    a, b = record("a", "deep learning for retrieval"), record("b", "other")
    fused = fuse([b, a], [a], w_dense=1, w_lex=.25)
    assert fused[0]["item_key"] == "a"
    assert fused[0]["ranking_score"] == pytest.approx(1/62 + .25/61)
    assert fuse([b, a], [a], query="deep learning for retrieval", w_lex=0, gate=True)[0]["item_key"] == "a"
    assert fuse([b, a], [a], query="deep learning", w_lex=0, gate=True)[0]["item_key"] == "b"
    assert fuse([b, a], [a], query="deep learning retrieval", w_lex=0, gate=True, gate_threshold=.8)[0]["item_key"] == "b"


def test_collapse_refill_aliases_and_conflicts():
    rows = [record("a", "A paper", "https://doi.org/10/x"), record("a"), record("b", "A PAPER", "10/x"),
            record("c", "Other", "10/x"), record("d", "Fourth"), record("e", "Fifth")]
    results = collapse_results(rows, 3)
    assert [r["item_key"] for r in results] == ["a", "d", "e"]
    assert results[0]["aliases"] == ["b", "c"]
    conflict = [record("a", "Same", "10/a"), record("b", "Same"), record("c", "Same", "10/b")]
    assert len(collapse_results(conflict, 10)) == 3
    assert len(collapse_results([record("a", "Same"), record("b", "same", "10/a")], 10)) == 1
    assert len(collapse_results([record("a"), record("b")], 10)) == 2


def test_noise():
    rows = [record("a", "  pRoQuEsT: clip"), record("b", "Paper"), record("c", "ProQuest: second")]
    assert is_web_clip(rows[0]["metadata"])
    assert [r["item_key"] for r in select_results(rows, 3)] == ["a", "b", "c"]
    assert [r["item_key"] for r in select_results(rows, 3, noise_policy="demote")] == ["b", "a", "c"]
    assert [r["item_key"] for r in select_results(rows, 3, noise_policy="exclude")] == ["b"]
    with pytest.raises(ValueError):
        select_results(rows, 3, noise_policy="bad")


@pytest.fixture
def search():
    client = ChromaClient.__new__(ChromaClient)
    client.embedding_function = None
    client.query_embedder = MagicMock(return_value=[1., 0., 0.])
    backend = chromadb.EphemeralClient(settings=Settings(anonymized_telemetry=False))
    import uuid
    collection = backend.create_collection("test-" + uuid.uuid4().hex, embedding_function=None)
    collection.add(ids=["a", "b", "c", "d"], documents=["vector", "deep learning retrieval", "deep learning retrieval", "Other"],
                   metadatas=[{"title": "Vector", "type": "paper"}, {"title": "deep learning retrieval", "type": "paper"},
                              {"title": "deep learning retrieval", "type": "clip"}, {"title": "Other"}],
                   embeddings=[[1., 0., 0.], [0., 1., 0.], [0., 0., 1.], [.7, .7, 0.]])
    client.collection = collection
    zotero = MagicMock()
    zotero.item.side_effect = lambda key: {"key": key}
    return ZoteroSemanticSearch(chroma_client=client, zotero_client=zotero)


@pytest.mark.parametrize("where", [{"type": "paper"}, {"type": {"$ne": "paper"}}, {"type": {"$eq": "paper"}},
    {"$and": [{"type": {"$eq": "paper"}}, {"title": {"$ne": "Vector"}}]}, {"type": {"$in": ["paper"]}}, {"type": {"$nin": ["paper"]}}, {"absent": {"$ne": "value"}}])
def test_filter_parity(search, where):
    collection = search.chroma_client.collection
    stored = collection.get(include=["metadatas"])
    python_ids = {key for key, meta in zip(stored["ids"], stored["metadatas"], strict=True) if matches_filter(meta, where)}
    assert python_ids == set(collection.get(where=where)["ids"])
    search._retrieval_config = profile_config("hybrid-gated")
    output = search.search("deep learning retrieval", limit=4, filters=where)
    assert not output.get("error")
    assert {r["item_key"] for r in output["results"]} == python_ids


def test_unsupported_filter_fallback(search):
    where = {"$or": [{"type": "paper"}, {"type": "clip"}]}
    assert not supports_filter(where)
    search._retrieval_config = profile_config("hybrid-gated")
    output = search.search("deep learning retrieval", filters=where)
    assert not output.get("error")
    assert output["retrieval"]["lexical_filter_fallback"]
    assert all("ranking_score" not in r for r in output["results"])


def test_default_rankings_and_contract(search):
    raw = search.chroma_client.search(["deep learning retrieval"], n_results=4)
    default = search.search("deep learning retrieval", limit=4)
    assert default["results"] == search._enrich_search_results(raw, "deep learning retrieval")
    assert all(set(r) == {"item_key", "similarity_score", "matched_text", "metadata", "zotero_item", "query"} for r in default["results"])
    search._retrieval_config = profile_config("hybrid-gated-collapse")
    search.chroma_client.query_embedder.reset_mock()
    output = search.search("deep learning retrieval", limit=3)
    assert not output.get("error")
    assert output["results"][0]["item_key"] == "b"
    assert output["results"][0]["aliases"] == ["c"]
    assert len(output["results"]) == 3
    assert all("ranking_score" in r for r in output["results"])
    search.chroma_client.query_embedder.assert_called_once()
    for result in output["results"]:
        old = next(r for r in default["results"] if r["item_key"] == result["item_key"])
        assert result["similarity_score"] == old["similarity_score"]


def test_lexical_cache_refresh_same_count(search):
    collection = search.chroma_client.collection
    first = LexicalIndex.for_collection(collection, batch_size=2)
    assert first is LexicalIndex.for_collection(collection)
    collection.update(ids=["a"], documents=["new text"], metadatas=[{"title": "New"}], embeddings=[[1., 0., 0.]])
    second = LexicalIndex.for_collection(collection)
    assert first is not second
    assert second.search("new")[0]["item_key"] == "a"


@pytest.mark.parametrize("space,expected", [("l2", -1.), ("cosine", 0.), ("ip", 0.)])
def test_local_score(space, expected):
    assert local_similarity([0, 1], [1, 0], space) == expected


def test_lexical_only_vector_score_and_refill(search):
    collection = search.chroma_client.collection
    collection.add(ids=[f"x{i}" for i in range(110)], documents=["duplicate body"] * 100 + ["needle lexical candidate"] + [f"unique{i}" for i in range(9)],
                   metadatas=[{"title": "Repeated title"}] * 100 + [{"title": "needle lexical candidate"}] + [{"title": f"unique{i}"} for i in range(9)],
                   embeddings=[[1., i * .0001, 0.] for i in range(100)] + [[0., 1., 0.]] * 10)
    search._retrieval_config = profile_config("hybrid-gated-collapse")
    search.chroma_client.query_embedder.reset_mock()
    output = search.search("needle lexical candidate", limit=10)
    assert not output.get("error")
    assert len(output["results"]) == 10
    assert output["retrieval"]["candidate_depth"] > 100
    assert output["results"][0]["item_key"] == "x100"
    assert output["results"][0]["similarity_score"] == pytest.approx(-1.)
    repeated = next(r for r in output["results"] if r["metadata"]["title"] == "Repeated title")
    assert len(repeated["aliases"]) == 99
    search.chroma_client.query_embedder.assert_called_once()


def test_profiles_and_overrides():
    assert profile_config("baseline-dense")["mode"] == "dense"
    assert profile_config("hybrid-rrf")["weights"]["lex"] == 1.
    assert profile_config("hybrid-weighted", ["weights.lex=0.25"])["weights"]["lex"] == .25
    assert profile_config("hybrid-gated-collapse")["gate"]
    assert profile_config("dense-collapse")["collapse"]
    with pytest.raises(ValueError, match="Unknown retrieval override"):
        profile_config("hybrid-weighted", ["bad=3"])


def test_evaluator_skip_missing_records_coverage(tmp_path, monkeypatch):
    import json

    import numpy as np

    from zotero_mcp import retrieval_eval as ev

    path = tmp_path / "snapshot" / "chroma_db"
    backend = chromadb.PersistentClient(path=str(path), settings=Settings(anonymized_telemetry=False))
    collection = backend.create_collection("zotero_library", embedding_function=None)
    collection.add(ids=["a"], embeddings=[[1., 0., 0.]], documents=["deep learning retrieval"],
                   metadatas=[{"title": "deep learning retrieval", "item_key": "a"}])
    suite = tmp_path / "suite.jsonl"
    suite.write_text(''.join(json.dumps(dict(query_id=q, text=t, partition="dev")) + '\n'
                             for q, t in [("cached", "deep learning retrieval"), ("missing", "uncached")]))
    cache = ev.QueryCache(tmp_path / "cache", dimension=3)
    cache.vectors[ev.text_key("deep learning retrieval")] = np.asarray([1., 0., 0.], dtype=np.float32)
    cache.metadata["entries"].append({"key": ev.text_key("deep learning retrieval"), "text": "deep learning retrieval"})
    cache.save()
    monkeypatch.setattr(ev, "QueryCache", lambda _: cache)
    monkeypatch.setattr(ev, "EXPECTED_COUNT", 1)
    output = tmp_path / "output"
    ev.run(tmp_path / "snapshot", suite, cache.directory, "hybrid-gated-collapse", output,
           skip_missing=True, partition="dev", overrides=["weights.lex=0.25"])
    manifest = json.loads((output / "rankings.manifest.json").read_text())
    assert manifest["skipped_query_ids"] == ["missing"]
    assert manifest["query_count"] == 1
    assert manifest["params"]["retrieval"]["weights"]["lex"] == .25
    assert len((output / "evaluated-suite.jsonl").read_text().splitlines()) == 1
    assert json.loads((output / "rankings.jsonl").read_text())["ranking"][0]["item_key"] == "a"
    assert manifest["snapshot_hashes"] == manifest["snapshot_hashes_after"]


def test_hybrid_optional_fields_preserve_mcp_markdown(search, monkeypatch, tmp_path, dummy_ctx):
    from zotero_mcp import semantic_search
    from zotero_mcp.tools import search as tool

    search._retrieval_config = profile_config("hybrid-gated-collapse")
    monkeypatch.setattr(tool, "get_config_path", lambda: tmp_path / "unused.json")
    monkeypatch.setattr(semantic_search, "create_semantic_search", lambda _: search)
    output = tool.semantic_search(query="deep learning retrieval", limit=2, ctx=dummy_ctx)
    assert output.startswith("# Semantic Search Results for 'deep learning retrieval'")
    assert "Found 2 similar items" in output
    assert "-1.000" in output
    assert "ranking_score" not in output


@pytest.mark.parametrize("change", ["filter", "delete", "refresh"])
def test_lexical_only_validated_after_fingerprint(search, monkeypatch, change):
    collection = search.chroma_client.collection
    build = LexicalIndex.for_collection

    def interleave(*args, **kwargs):
        index = build(*args, **kwargs)
        if change == "delete":
            collection.delete(ids=["b"])
        else:
            collection.update(ids=["b"], documents=["current document"], embeddings=[[.5, 0., 0.]],
                              metadatas=[{"title": "Current title", "type": "clip" if change == "filter" else "paper"}])
        return index

    monkeypatch.setattr(LexicalIndex, "for_collection", interleave)
    # Simulate an ANN prefix excluding b, making it a lexical-only candidate.
    dense_search = search.chroma_client.search
    monkeypatch.setattr(search.chroma_client, "search", lambda *a, **kw: dense_search(*a, **dict(kw, n_results=1)))
    lookup = MagicMock(wraps=collection.get)
    monkeypatch.setattr(collection, "get", lookup)
    search._retrieval_config = profile_config("hybrid-gated")
    output = search.search("deep learning retrieval", limit=4, filters={"type": "paper"})
    assert not output.get("error")
    results = {r["item_key"]: r for r in output["results"]}
    if change == "refresh":
        assert results["b"]["metadata"]["title"] == "Current title"
        assert results["b"]["matched_text"] == "current document"
        assert results["b"]["similarity_score"] == pytest.approx(.75)
    else:
        assert "b" not in results
    lookup.assert_any_call(ids=["b"], where={"type": "paper"}, include=["embeddings", "metadatas", "documents"])


@pytest.mark.parametrize("profile", ["hybrid-gated-collapse", "dense-collapse"])
def test_reranker_promotes_outside_limit_and_refills_pool(search, profile):
    collection = search.chroma_client.collection
    collection.add(ids=[f"dup{i}" for i in range(105)] + ["promoted"],
                   documents=["duplicate"] * 105 + ["promote me"],
                   metadatas=[{"title": "Vector"}] * 105 + [{"title": "Promoted"}],
                   embeddings=[[1., 0., 0.]] * 105 + [[0., 0., -2.]])
    search._retrieval_config = profile_config(profile)
    search._reranker_config["enabled"] = True
    search._reranker_config["candidate_multiplier"] = 4
    search._reranker = MagicMock()

    def rerank(query, documents, top_k):
        assert len(documents) == top_k == 4
        promoted = documents.index("promote me")
        return [promoted] + [i for i in range(top_k) if i != promoted]

    search._reranker.rerank.side_effect = rerank
    output = search.search("unmatched query", limit=1)
    assert not output.get("error")
    assert output["retrieval"]["candidate_depth"] > 100
    assert [r["item_key"] for r in output["results"]] == ["promoted"]
    search._reranker.rerank.assert_called_once()


def cached_collection(identity):
    collection = MagicMock()
    collection.id = identity
    collection.count.return_value = 1
    collection.get.return_value = dict(ids=[identity], documents=["needle"], metadatas=[{"title": "needle"}])
    return collection


def test_lexical_cache_bounded_lru(monkeypatch):
    from collections import OrderedDict

    monkeypatch.setattr(LexicalIndex, "_cache", OrderedDict())
    a, b, c = [cached_collection(key) for key in "abc"]
    first = LexicalIndex.for_collection(a)
    evicted = LexicalIndex.for_collection(b)
    connection = evicted._connection
    del evicted
    assert LexicalIndex.for_collection(a) is first
    LexicalIndex.for_collection(c)
    assert list(LexicalIndex._cache) == ["a", "c"]
    with pytest.raises(Exception, match="closed database"):
        connection.execute("SELECT 1")
    for i in range(10):
        LexicalIndex.for_collection(cached_collection(str(i)), cache_size=3)
        assert len(LexicalIndex._cache) <= 3
    LexicalIndex.for_collection(a)
    assert len(LexicalIndex._cache) == 2
    with pytest.raises(ValueError, match="positive integer"):
        LexicalIndex.for_collection(a, cache_size=0)


def test_lexical_search_survives_concurrent_eviction(monkeypatch):
    import weakref
    from collections import OrderedDict
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from zotero_mcp import retrieval

    monkeypatch.setattr(LexicalIndex, "_cache", OrderedDict())
    index = LexicalIndex.for_collection(cached_collection("active"), cache_size=1)
    connection = index._connection
    reference = weakref.ref(index)
    entered, resume = Event(), Event()
    original = retrieval.matches_filter

    def pause(metadata, where):
        entered.set()
        assert resume.wait(10)
        return original(metadata, where)

    monkeypatch.setattr(retrieval, "matches_filter", pause)
    with ThreadPoolExecutor(max_workers=2) as executor:
        running = executor.submit(index.search, "needle")
        try:
            assert entered.wait(10)
            del index
            for i in range(5):
                LexicalIndex.for_collection(cached_collection(str(i)), cache_size=1)
                assert len(LexicalIndex._cache) == 1
            assert "active" not in LexicalIndex._cache
        finally:
            resume.set()
        assert running.result(timeout=10)[0]["item_key"] == "active"
    assert reference() is None
    with pytest.raises(Exception, match="closed database"):
        connection.execute("SELECT 1")

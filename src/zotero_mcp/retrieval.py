"""Memory-only lexical retrieval, conservative identity collapse, and weighted RRF."""

import hashlib
import json
import re
import sqlite3
import time
import unicodedata

import numpy as np


def tokens(text):
    return re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", str(text or "")).casefold(), re.UNICODE)


def normalized_title(text):
    return " ".join(tokens(text))


def normalized_doi(value):
    value = str(value or "").strip().casefold()
    return re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value).strip()


def is_web_clip(metadata):
    """The explicit noise proxy; item_type is not evidence of a web clip."""
    return str(metadata.get("title", "")).lstrip().casefold().startswith("proquest:")


def supports_filter(where):
    if where is None:
        return True
    if not isinstance(where, dict) or len(where) != 1:
        return False
    key, value = next(iter(where.items()))
    if key == "$and":
        return isinstance(value, list) and len(value) >= 2 and all(supports_filter(v) for v in value)
    if key.startswith("$"):
        return False
    if isinstance(value, dict):
        if len(value) != 1:
            return False
        op, operand = next(iter(value.items()))
        if op in {"$in", "$nin"}:
            return isinstance(operand, list) and bool(operand)
        return op in {"$eq", "$ne"} and isinstance(operand, (str, int, float, bool))
    return isinstance(value, (str, int, float, bool))


def matches_filter(metadata, where):
    """Chroma metadata predicates, including missing-field negative comparisons."""
    if not supports_filter(where):
        raise ValueError("Unsupported lexical metadata filter")
    if where is None:
        return True
    key, value = next(iter(where.items()))
    if key == "$and":
        return all(matches_filter(metadata, clause) for clause in value)
    op, operand = next(iter(value.items())) if isinstance(value, dict) else ("$eq", value)
    if key not in metadata:
        return op in {"$ne", "$nin"}
    actual = metadata[key]
    # Chroma separates boolean metadata from numeric metadata.
    def equal(x):
        return actual == x and isinstance(actual, bool) == isinstance(x, bool)

    if op == "$eq":
        return equal(operand)
    if op == "$ne":
        return not equal(operand)
    if op == "$in":
        return any(equal(x) for x in operand)
    return not any(equal(x) for x in operand)


class LexicalIndex:
    """FTS5 unicode61 index, refreshed on any stored text/metadata change.

    Fingerprinting uses batched reads on lookup so same-count replacements cannot
    silently reuse a stale index. Vectors are fetched only for lexical candidates.
    No SQLite file is created. The cache retains one version per collection identity.
    """

    _cache = {}

    @classmethod
    def for_collection(cls, collection, batch_size=500):
        started = time.perf_counter()
        count = collection.count()
        records = []
        digest = hashlib.sha256()
        for offset in range(0, count, batch_size):
            batch = collection.get(limit=batch_size, offset=offset, include=["documents", "metadatas"])
            for key, document, metadata in zip(batch["ids"], batch["documents"], batch["metadatas"], strict=True):
                record = (key, document or "", metadata or {})
                digest.update(json.dumps(record, sort_keys=True, ensure_ascii=False).encode())
                records.append(record)
        identity = str(getattr(collection, "id", None) or id(collection))
        cache_key = (identity, count, digest.hexdigest())
        previous = cls._cache.get(identity)
        if previous and previous[0] == cache_key:
            index = previous[1]
        else:
            index = cls(records)
            cls._cache[identity] = (cache_key, index)
        index.fingerprint_seconds = time.perf_counter() - started
        return index

    def __init__(self, records):
        started = time.perf_counter()
        self.connection = sqlite3.connect(":memory:", check_same_thread=False)
        try:
            self.connection.execute("CREATE VIRTUAL TABLE fts USING fts5(title, document, tokenize='unicode61')")
        except sqlite3.OperationalError as error:
            self.connection.close()
            if "no such module: fts5" not in str(error):
                raise
            # Some managed Python builds omit FTS5. Use a private connection;
            # never replace sys.modules['sqlite3'] or alter Chroma's SQLite.
            try:
                import pysqlite3
            except ImportError:
                raise RuntimeError("Hybrid retrieval requires SQLite FTS5; install the semantic extra or use an FTS5-enabled Python") from error
            self.connection = pysqlite3.connect(":memory:", check_same_thread=False)
            self.connection.execute("CREATE VIRTUAL TABLE fts USING fts5(title, document, tokenize='unicode61')")
        self.records = {}
        for rowid, (key, document, metadata) in enumerate(records, 1):
            self.connection.execute("INSERT INTO fts(rowid,title,document) VALUES (?,?,?)",
                                    (rowid, metadata.get("title", ""), document))
            self.records[rowid] = dict(item_key=key, matched_text=document, metadata=metadata)
        self.build_seconds = time.perf_counter() - started

    def search(self, query, limit=100, where=None, title_weight=3.0):
        started = time.perf_counter()
        self.last_search_seconds = 0.0
        terms = list(dict.fromkeys(tokens(query)))
        if not terms:
            return []
        expression = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
        rows = self.connection.execute(
            "SELECT rowid, bm25(fts, ?, 1.0) FROM fts WHERE fts MATCH ? ORDER BY 2, rowid",
            (title_weight, expression),
        )
        results = []
        for rowid, score in rows:
            record = self.records[rowid]
            if matches_filter(record["metadata"], where):
                results.append(dict(record, lexical_score=-score))
                if len(results) >= limit:
                    break
        self.last_search_seconds = time.perf_counter() - started
        return results


def fuse(dense, lexical, query="", w_dense=1.0, w_lex=0.5, k=60,
         gate=False, gate_threshold=0.8, gate_min_tokens=3):
    """Weighted reciprocal ranks; stable ties follow dense then lexical order."""
    if k < 0 or min(w_dense, w_lex) < 0:
        raise ValueError("RRF weights and k must be nonnegative")
    scores, records = {}, {}
    for branch, weight in ((dense, w_dense), (lexical, w_lex)):
        seen = set()
        for rank, record in enumerate(branch, 1):
            key = record["item_key"]
            if key in seen:
                continue
            seen.add(key)
            records.setdefault(key, record)
            scores[key] = scores.get(key, 0.0) + weight / (k + rank)
    ordered = sorted(records, key=lambda key: -scores[key])
    gated = None
    if gate and lexical:
        title_terms = set(tokens(lexical[0]["metadata"].get("title", "")))
        overlap = title_terms & set(tokens(query))
        if len(overlap) >= gate_min_tokens and len(overlap) / max(len(title_terms), 1) >= gate_threshold:
            gated = lexical[0]["item_key"]
            ordered.remove(gated)
            ordered.insert(0, gated)
            scores[gated] = max(scores.values()) + 1 / (k + 1)
    return [dict(records[key], ranking_score=scores[key], ranking_method="hybrid-gated" if key == gated else "hybrid-rrf")
            for key in ordered]


def collapse_results(results, limit):
    """Keep best-ranked representatives; never bridge two conflicting DOIs.

    A title with multiple known DOIs is ambiguous: DOI-less members of that title
    remain a separate group. Same-DOI members may have different titles.
    Aliases are item keys, excluding the representative itself.
    """
    unique = {}
    for result in results:
        unique.setdefault(result["item_key"], result)
    title_dois = {}
    for result in unique.values():
        meta = result["metadata"]
        title, doi = normalized_title(meta.get("title", "")), normalized_doi(meta.get("DOI", meta.get("doi")))
        if title and doi:
            title_dois.setdefault(title, set()).add(doi)
    groups = {}
    for result in unique.values():
        meta = result["metadata"]
        title, doi = normalized_title(meta.get("title", "")), normalized_doi(meta.get("DOI", meta.get("doi")))
        known = title_dois.get(title, set())
        identity = ("doi", doi) if doi else (
            ("doi", next(iter(known))) if title and len(known) == 1 else
            ("title", title) if title else ("key", result["item_key"]))
        if identity not in groups:
            groups[identity] = dict(result, aliases=list(result.get("aliases", [])))
        else:
            aliases = groups[identity]["aliases"]
            aliases.extend(key for key in [result["item_key"], *result.get("aliases", [])] if key not in aliases)
    return list(groups.values())[:limit]


def select_results(results, limit, collapse=False, noise_policy="keep"):
    if noise_policy not in {"keep", "demote", "exclude"}:
        raise ValueError("noise_policy must be keep, demote, or exclude")
    if noise_policy == "exclude":
        results = [r for r in results if not is_web_clip(r["metadata"])]
    elif noise_policy == "demote":
        results = sorted(results, key=lambda r: is_web_clip(r["metadata"]))
    return collapse_results(results, limit) if collapse else results[:limit]


def local_similarity(vector, query, space="l2"):
    """Preserve the legacy 1 - collection distance score, including squared L2."""
    vector, query = np.asarray(vector), np.asarray(query)
    if space == "cosine":
        return float(vector @ query / max(np.linalg.norm(vector) * np.linalg.norm(query), 1e-30))
    if space == "ip":
        return float(vector @ query)
    return 1.0 - float(np.sum((vector - query) ** 2))

"""Contract-v1 text-query evaluator. Only embed-queries may contact a provider.

Run always copies the source before opening Chroma and uses cached query vectors.
The production Gemini query path performs identity preprocessing (no truncation).
"""

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import socket
import stat
import subprocess
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import numpy as np

from .chroma_client import GEMINI_DIRECT_ENDPOINT, ChromaClient, GeminiEmbeddingFunction

MODEL = "gemini-embedding-2-preview"
EXPECTED_COUNT = 3309


def text_key(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_suite(path):
    rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    ids = [row["query_id"] for row in rows]
    if len(ids) != len(set(ids)) or any(not isinstance(row["text"], str) or not row["text"] for row in rows):
        raise ValueError("Suite requires unique query IDs and nonempty text")
    return rows


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


class QueryCache:
    def __init__(self, directory, model=MODEL, dimension=3072):
        self.directory = Path(directory)
        self.metadata = dict(
            model=model,
            task_type="retrieval_query",
            dimension=dimension,
            route="gemini-direct",
            endpoint=GEMINI_DIRECT_ENDPOINT,
            preprocessing="v1",
            entries=[],
            usage={},
            est_cost_usd=0.0,
        )
        self.vectors = {}
        current = self.directory / "CURRENT"
        has_current = current.exists()
        self.generation = current.read_text().strip() if has_current else "0"
        if self.generation != "0" and (
            len(self.generation) != 32 or any(c not in "0123456789abcdef" for c in self.generation)
        ):
            raise ValueError("Invalid cache generation")
        self.npz_path = self.directory / (
            "query-vectors.npz" if self.generation == "0" else f"vectors-{self.generation}.npz"
        )
        npz = self.npz_path
        meta = self.directory / (
            "query-vectors.json" if self.generation == "0" else f"meta-{self.generation}.json"
        )
        if has_current and not (npz.exists() and meta.exists()):
            raise ValueError("Incomplete committed query cache")
        if npz.exists() != meta.exists():
            raise ValueError("Incomplete query cache")
        if not npz.exists():
            return
        self.metadata = json.loads(meta.read_text())
        for key, expected in (
            ("model", model),
            ("dimension", dimension),
            ("task_type", "retrieval_query"),
            ("route", "gemini-direct"),
            ("preprocessing", "v1"),
        ):
            if self.metadata[key] != expected:
                raise ValueError(f"Cache {key} mismatch")
        with np.load(npz, allow_pickle=False) as data:
            keys, vectors = data["keys"], data["vectors"]
            if (
                set(data.files) != {"keys", "vectors"}
                or keys.dtype.kind != "U"
                or keys.ndim != 1
                or vectors.dtype != np.float32
                or vectors.shape != (len(keys), dimension)
                or not np.isfinite(vectors).all()
                or len(set(keys)) != len(keys)
            ):
                raise ValueError("Invalid cache arrays")
            self.vectors = dict(zip(keys.tolist(), vectors, strict=True))
        entries = self.metadata["entries"]
        if len(entries) != len(keys) or {e["key"] for e in entries} != set(keys):
            raise ValueError("Cache entries do not match arrays")
        for entry in entries:
            if text_key(entry["text"]) != entry["key"]:
                raise ValueError("Cache text checksum mismatch")
            checksum = entry.get("vector_checksum")
            if checksum and checksum != hashlib.sha256(self.vectors[entry["key"]].tobytes()).hexdigest():
                raise ValueError("Cache vector checksum mismatch")

    def embed_query(self, text):
        try:
            return self.vectors[text_key(text)].tolist()
        except KeyError:
            raise ValueError(f"Query cache miss: {text_key(text)}") from None

    def save(self):
        """Publish immutable files before CURRENT; retain older generations for readers.

        CURRENT is authoritative. The contract exports are individually atomic, but
        readers needing a consistent pair across concurrent commits must use CURRENT.
        A publication error may leave the old or the new complete generation visible.
        """
        durable_directory(self.directory)
        generation = uuid.uuid4().hex
        keys = list(self.vectors)
        vectors = np.asarray(list(self.vectors.values()), dtype=np.float32).reshape(-1, self.metadata["dimension"])
        npz = self.directory / f"vectors-{generation}.npz"
        meta = self.directory / f"meta-{generation}.json"
        with atomic_file(npz) as stream:
            np.savez(stream, keys=np.asarray(keys, dtype="U64"), vectors=vectors)
        with atomic_file(meta) as stream:
            stream.write((json.dumps(self.metadata, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode())
        # Persist both immutable names before a pointer to them can become durable.
        fsync_directory(self.directory)
        with atomic_file(self.directory / "CURRENT") as stream:
            stream.write((generation + "\n").encode())
        fsync_directory(self.directory)
        self.generation, self.npz_path = generation, npz
        for source, name in ((npz, "query-vectors.npz"), (meta, "query-vectors.json")):
            with atomic_file(self.directory / name) as stream, source.open("rb") as original:
                shutil.copyfileobj(original, stream)
        fsync_directory(self.directory)


def fsync_directory(directory):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def durable_directory(directory):
    # Fsync newly created directory names too, before any paid reservations.
    missing = []
    parent = directory
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    directory.mkdir(parents=True, exist_ok=True)
    for path in reversed(missing):
        fsync_directory(path.parent)


@contextmanager
def atomic_file(path):
    """Flush a temporary sibling before atomically replacing its destination."""
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            yield stream
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def cache_lock(directory, fail_fast=False):
    directory = Path(directory)
    durable_directory(directory)
    with (directory / ".lock").open("a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | (fcntl.LOCK_NB if fail_fast else 0))
        except BlockingIOError:
            raise BlockingIOError("Query cache is locked by another fill") from None
        try:
            # Existing names may come from an interrupted or concurrent mkdir.
            # Make the entire ancestry durable before loading or reserving spend.
            resolved = directory.resolve()
            for path in (resolved, *resolved.parents):
                fsync_directory(path)
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def embed_queries(
    suite, cache, max_cost_usd=0.90, price_per_million_tokens=0.20, model=MODEL, dimension=3072, fail_fast=False
):
    # Lock before loading: a waiting writer must see all previous reservations.
    with cache_lock(cache, fail_fast):
        return _fill_queries(suite, cache, max_cost_usd, price_per_million_tokens, model, dimension)


def _fill_queries(suite, cache, max_cost_usd, price_per_million_tokens, model, dimension):
    if not 0 <= max_cost_usd <= 0.90 or price_per_million_tokens <= 0:
        raise ValueError("Cost ceiling must be between $0 and $0.90; price must be positive")
    store = QueryCache(cache, model, dimension)
    texts = list(dict.fromkeys(row["text"] for row in read_suite(suite)))
    misses = [text for text in texts if text_key(text) not in store.vectors]
    # Conservative upper estimate: one token per UTF-8 byte plus request overhead.
    estimates = {text: len(text.encode("utf-8")) + 32 for text in misses}
    unit = price_per_million_tokens / 1_000_000
    if store.metadata["est_cost_usd"] + sum(estimates.values()) * unit > max_cost_usd:
        raise ValueError("Estimated query cost exceeds cap")
    if not misses:
        if not (Path(cache) / "query-vectors.npz").exists():
            store.save()
        return store
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY is required")
    # Pin direct route even if the operator has a GEMINI_BASE_URL override.
    with patch.dict(os.environ, {"GOOGLE_GENAI_USE_VERTEXAI": "false"}):
        embedder = GeminiEmbeddingFunction(
            model_name=model, api_key=api_key, base_url=GEMINI_DIRECT_ENDPOINT.removesuffix("/v1beta"), max_attempts=1
        )
    for text in misses:
        for attempt in range(3):
            charge = estimates[text] * unit
            if store.metadata["est_cost_usd"] + charge > max_cost_usd:
                raise ValueError("Query cost cap exhausted (including retries)")
            # Persist an attempted request before issuing it, including failed requests.
            store.metadata["est_cost_usd"] += charge
            store.metadata["usage"]["attempts"] = store.metadata["usage"].get("attempts", 0) + 1
            store.save()
            try:
                vector = np.asarray(embedder.embed_query(text), dtype=np.float32)
            except Exception:
                if attempt == 2:
                    raise RuntimeError("Gemini query embedding failed after three attempts") from None
                time.sleep(2**attempt)
                continue
            if vector.shape != (dimension,) or not np.isfinite(vector).all():
                raise ValueError("Returned query dimension or values do not match cache contract")
            usage = {}
            for label, attribute in (("request", "last_query_usage"), ("statistics", "last_query_statistics")):
                value = getattr(embedder, attribute, None)
                if value is not None and hasattr(value, "model_dump"):
                    usage[label] = value.model_dump(mode="json", exclude_none=True)
            if usage:
                store.metadata["usage"].setdefault("responses", []).append(dict(key=text_key(text), **usage))
            key = text_key(text)
            store.vectors[key] = vector
            store.metadata["entries"].append(
                dict(
                    key=key,
                    text=text,
                    estimated_tokens=estimates[text],
                    vector_checksum=hashlib.sha256(vector.tobytes()).hexdigest(),
                )
            )
            store.save()
            break
    return store


def hash_tree(directory):
    """Hash every regular source file, rejecting links to outside the snapshot."""
    hashes = {}
    for path in sorted(Path(directory).rglob("*")):
        if path.is_symlink():
            raise ValueError("Snapshot must not contain symlinks")
        if path.is_file():
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            hashes[str(path.relative_to(directory))] = digest.hexdigest()
    return hashes


@contextmanager
def offline():
    def deny(*args, **kwargs):
        raise RuntimeError("Network access is disabled during retrieval evaluation")

    with (
        patch.object(socket.socket, "connect", deny),
        patch.object(socket.socket, "connect_ex", deny),
        patch.object(socket, "create_connection", deny),
    ):
        yield


class SnapshotZotero:
    def __init__(self, records):
        self.items = {
            key: {"key": key, "data": dict(metadata or {})}
            for key, metadata in zip(records["ids"], records["metadatas"], strict=True)
        }

    def item(self, key):
        return self.items[key]


def eligible_ids(row, ids):
    eligible = set(ids)
    if row.get("candidate_ids") is not None:
        eligible.intersection_update(row["candidate_ids"])
    return eligible.difference(row.get("exclude_ids", []))


def run(snapshot, suite, cache, profile, output, k=100):
    if profile != "baseline-dense" or not 1 <= k <= 100:
        raise ValueError("Only baseline-dense and 1 <= k <= 100 are supported")
    rows = read_suite(suite)
    store = QueryCache(cache)
    for row in rows:
        store.embed_query(row["text"])
    snapshot, output = Path(snapshot).resolve(), Path(output).resolve()
    if output == snapshot or snapshot in output.parents:
        raise ValueError("Output must be outside the snapshot")
    output.mkdir(parents=True, exist_ok=False)
    source = snapshot / "chroma_db"
    before = hash_tree(snapshot)
    scratch = output / "scratch" / "chroma_db"
    os.environ["ANONYMIZED_TELEMETRY"] = "False"
    try:
        shutil.copytree(source, scratch)
        if hash_tree(source) != hash_tree(scratch):
            raise ValueError("Scratch copy does not match source")
        # copytree preserves snapshot permissions; Chroma must be able to write its copy.
        for path in [scratch, *scratch.rglob("*")]:
            writable = stat.S_IWUSR | (stat.S_IXUSR if path.is_dir() else 0)
            path.chmod(path.stat().st_mode | writable)
        with offline():
            # Import inside network denial: optional tokenizer initialization must stay offline too.
            from .semantic_search import ZoteroSemanticSearch

            client = ChromaClient.attach_existing(str(scratch), query_embedder=store.embed_query)
            if client.collection.count() != EXPECTED_COUNT:
                raise ValueError(f"Expected {EXPECTED_COUNT} collection records")
            records = client.collection.get(include=["embeddings", "metadatas"])
            ids = records["ids"]
            if len(set(ids)) != EXPECTED_COUNT:
                raise ValueError("Collection IDs are incomplete or duplicated")
            vectors = np.asarray(records["embeddings"], dtype=np.float32)
            if vectors.shape != (EXPECTED_COUNT, store.metadata["dimension"]):
                raise ValueError("Collection vector dimension mismatch")
            # Membership constraints use item_key, which must be identical to the record ID.
            if any((meta or {}).get("item_key") != key for key, meta in zip(ids, records["metadatas"], strict=True)):
                raise ValueError("Snapshot item_key metadata does not match record IDs")
            search = ZoteroSemanticSearch(chroma_client=client, zotero_client=SnapshotZotero(records))
            overlaps = []
            with (output / "rankings.jsonl").open("w") as stream:
                for row in rows:
                    eligible = eligible_ids(row, ids)
                    filters = {"item_key": {"$in": sorted(eligible)}} if len(eligible) != len(ids) else None
                    result = search.search(row["text"], limit=k, filters=filters) if eligible else {"results": []}
                    if result.get("error"):
                        raise RuntimeError(result["error"])
                    ranking = [dict(item_key=r["item_key"], score=r["similarity_score"]) for r in result["results"]]
                    ranked_ids = [r["item_key"] for r in ranking]
                    if len(set(ranked_ids)) != len(ranked_ids) or not set(ranked_ids) <= eligible:
                        raise ValueError("Duplicate or ineligible retrieval results")
                    query = np.asarray(store.embed_query(row["text"]), dtype=np.float32)
                    cosine = (
                        vectors @ query / np.maximum(np.linalg.norm(vectors, axis=1) * np.linalg.norm(query), 1e-30)
                    )
                    exact = [ids[i] for i in np.argsort(-cosine, kind="stable") if ids[i] in eligible][:10]
                    overlaps.append(len(set(exact) & set(ranked_ids[:10])) / len(exact) if exact else 1.0)
                    stream.write(
                        json.dumps(
                            dict(query_id=row["query_id"], system=f"zmcp-{profile}", ranking=ranking),
                            ensure_ascii=False,
                            allow_nan=False,
                        )
                        + "\n"
                    )
    finally:
        after = hash_tree(snapshot)
        if before != after:
            raise RuntimeError("Source snapshot changed during evaluation")
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=Path(__file__).parent, text=True).strip()
    write_json(
        output / "rankings.manifest.json",
        dict(
            system=f"zmcp-{profile}",
            code_revision=revision,
            params=dict(
                profile=profile,
                k=k,
                collection_name="zotero_library",
                model=store.metadata["model"],
                dimension=store.metadata["dimension"],
                reranker=False,
            ),
            snapshot_hashes=before,
            snapshot_hashes_after=after,
            suite_sha256=hashlib.sha256(Path(suite).read_bytes()).hexdigest(),
            cache_sha256=hashlib.sha256(store.npz_path.read_bytes()).hexdigest(),
            ann_vs_exact_overlap_at_10=float(np.mean(overlaps)) if overlaps else None,
            diagnostic_note="Cosine exact reference; production retains the collection metric. For k<10 overlap is depth-limited.",
            query_count=len(rows),
            collection_count=len(ids),
        ),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    embed = sub.add_parser("embed-queries")
    embed.add_argument("--suite", required=True)
    embed.add_argument("--cache", required=True)
    embed.add_argument("--max-cost-usd", type=float, default=0.90)
    embed.add_argument("--price-per-million-tokens", type=float, default=0.20)
    embed.add_argument("--model", default=MODEL)
    embed.add_argument("--dimension", type=int, default=3072)
    embed.add_argument("--fail-fast", action="store_true", help="Fail immediately if another cache fill holds the lock")
    runner = sub.add_parser("run")
    for name in ("snapshot", "suite", "cache", "output"):
        runner.add_argument(f"--{name}", required=True)
    runner.add_argument("--profile", choices=["baseline-dense"], required=True)
    runner.add_argument("--k", type=int, default=100)
    args = vars(parser.parse_args())
    command = args.pop("command")
    (embed_queries if command == "embed-queries" else run)(**args)


if __name__ == "__main__":
    main()

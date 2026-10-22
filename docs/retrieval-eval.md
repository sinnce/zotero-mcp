# Text-query retrieval evaluation

`python -m zotero_mcp.retrieval_eval` implements the shared v1 suite/cache/rankings
contract. This evaluator preserves dense ranking, including the existing distance
score conversion. It does not embed corpus documents or read user configuration.
Only `embed-queries` contacts Gemini. The key must already be in `GEMINI_API_KEY`.

```bash
cd "$HOME/workspace/zotero-mcp-wt/retrieval-eval"
eval_root="$HOME/workspace/np-orch/2026-10-23/pkb/eval"
uv run --offline --no-sync python -m zotero_mcp.retrieval_eval embed-queries \
  --suite "$eval_root/suite.jsonl" --cache "$eval_root/zmcp/query-cache" \
  --max-cost-usd 0.90 --price-per-million-tokens 0.20 \
  --model gemini-embedding-2-preview --dimension 3072
uv run --offline --no-sync python -m zotero_mcp.retrieval_eval run \
  --snapshot "$HOME/workspace/np-orch/2026-10-23/pkb/snapshot" \
  --suite "$eval_root/suite.jsonl" --cache "$eval_root/zmcp/query-cache" \
  --profile baseline-dense --output "$eval_root/zmcp/baseline-dense" --k 100
```

The output directory must not exist. The suite path must point to the file exported
by paper-graph; change that path if the exporter places it elsewhere. Run these
commands only after that suite exists. `uv --offline` prevents dependency resolution
from contacting package servers; the embedding command still contacts Gemini.

Query inputs are sent unchanged through `GeminiEmbeddingFunction.embed_query`,
with `retrieval_query` and no explicit output dimension. The dimension argument
validates the returned shape, rather than changing the production API request.
Cache keys hash exact UTF-8 text. Model, route, preprocessing, dimensions, and
checksums are validated when loading. Identical text across queries is embedded
once. Cache misses during evaluation fail before a database is opened.

Budget accounting reserves one token per UTF-8 byte plus 32 tokens per request,
charges every attempted request before issuing it, and persists those charges so
restarts and retries consume the same $0.90 ceiling. SDK usage metadata is retained
when supplied. This is conservative estimated billing, not a provider invoice.
There are at most three attempts per text; no batch API or model fallback is used.
A partially filled cache can be resumed with the same settings.

`run` copies only `snapshot/chroma_db` into its output scratch directory, makes only that copy writable, verifies
the copy, and hashes every snapshot file before and after execution. Chroma opens
only the scratch copy, with telemetry disabled and no embedding function attached.
The runner requires 3,309 unique records and validates that metadata item keys match
record IDs. Enrichment uses only metadata fetched from that scratch collection.
Socket connections are disabled during retrieval. Candidate sets and exclusions
become Chroma item-key filters; relevance labels never reach retrieval.

The manifest records the Git revision, parameters, source hashes, suite/cache
checksums, and mean ANN overlap with exact cosine top-10 over eligible vectors.
This is a diagnostic: the existing collection metric still determines production
ranking. With `k < 10`, overlap is limited by requested result depth. Ties in the
exact reference follow collection ID order. Scores retain `1 - distance` semantics.
An interrupted or failed run can leave an output directory; use a fresh directory
for a subsequent run. A manifest is written only after successful verification.

## Opt-in hybrid retrieval

Production defaults remain `mode=dense`, `collapse=false`, `noise_policy=keep`.
An explicit configuration can enable the new path:

```json
{"semantic_search":{"retrieval":{"mode":"hybrid","collapse":true,"noise_policy":"keep","weights":{"dense":1.0,"lex":0.5},"k":60,"title_weight":3.0,"gate":true,"gate_threshold":0.8,"gate_min_tokens":3}}}
```

The lexical branch builds a SQLite FTS5 `unicode61` index in memory from stored
titles and documents. It quotes individual query tokens and ORs them. A batched
content fingerprint detects changes even when collection counts do not change.
The process cache is keyed by collection identity, count and fingerprint. There
are no ingestion or corpus embedding changes.

Hybrid retrieval initially fetches 100 candidates from each branch, uses weighted
reciprocal ranks, and optionally forces the top lexical candidate to rank 1 when
at least three distinct query tokens cover the configured fraction of its title.
Metadata `$eq`, `$ne`, `$and`, `$in` and `$nin` filters apply to both branches;
unsupported filters use dense retrieval and set `retrieval.lexical_filter_fallback`.
Missing metadata satisfies negative `$ne`/`$nin` comparisons and fails positive comparisons. Collapse preserves the first
ranked representative and records other keys in `aliases`. Same DOIs merge;
titles merge only without conflicting DOIs. DOI-less records with an ambiguous
title remain separate from its conflicting DOI groups. Empty titles never merge.
Noise uses only the left-trimmed, case-insensitive `ProQuest:` title prefix.
`demote` moves matching candidates after other candidates, and `exclude` removes
them. Suppression refills from deeper candidates until the requested limit or
collection exhaustion.

`similarity_score` still means `1 - collection distance`, including locally scored
lexical-only vectors. `ranking_score` and `ranking_method` describe hybrid order;
MCP Markdown remains unchanged. The default dense path has no added fields.

The evaluator supports `baseline-dense`, `dense-collapse`, `hybrid-rrf` (equal
weights), `hybrid-weighted`, `hybrid-gated`, `hybrid-weighted-collapse`, and
`hybrid-gated-collapse`. Weighted profiles start at lexical weight 0.5; gated
profiles also enable the 0.8 title gate. These are hypotheses, not changed defaults.
All resolved retrieval settings, skipped query IDs, full search latencies, and
lexical build/fingerprint/query timings are recorded in rankings manifests.
Uncommitted implementation file hashes are also recorded for reproducibility.
For hybrid/collapse profiles, the overlap diagnostic compares final returned
rankings with exact cosine neighbors; it does not isolate ANN recall.

For dev-only tuning, use a fresh output directory for each trial:

```bash
uv run --no-sync python -m zotero_mcp.retrieval_eval run \
  --snapshot "$snapshot_root" --suite "$suite" --cache "$cache" \
  --profile hybrid-gated --partition dev --set weights.lex=0.25 \
  --set gate_threshold=0.9 --output "$trial_output" --k 100
```

`--skip-missing` omits queries without cached vectors and records their IDs. Every
run writes `evaluated-suite.jsonl` alongside `suite.manifest.json`; pass this suite
to paper-graph scoring when using a partition or partial cache. This preserves
exact query coverage for the scorer. Never treat missing vectors as empty retrieval
rankings. Compare trials on identical query coverage, select parameters on dev
only, then evaluate test queries once with the selected parameters.

SQLite must support FTS5. On Linux, the semantic extra includes `pysqlite3-binary`
for managed Python builds whose builtin SQLite omits FTS5. The fallback is private
to the memory index and does not replace Chroma's SQLite module.

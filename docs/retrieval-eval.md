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

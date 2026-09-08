# Read-only Paper Graph snapshot export

`export-snapshot` emits the complete producer handoff consumed by Paper
Graph's fresh canonical storage builder. It reads the configured Zotero
library and an explicitly selected sealed Chroma index. It does not update Zotero, create a
collection, initialize an embedding function, re-embed text, or fill missing
vectors. The Chroma API opens a temporary content-fingerprinted clone because
even read calls can update Chroma files. Schema migrations are validation-only
on that clone; the source index is fingerprinted again before export succeeds.

```bash
zotero-mcp export-snapshot \
  --config-path ~/.config/zotero-mcp/config.json \
  --index-path ~/.config/zotero-mcp/chroma-recovery-YYYYMMDD \
  --output /tmp/zotero-paper-graph-snapshot.json
```

`--index-path` selects a completed isolated build containing `manifest.json`.
The manifest, rather than the active Chroma configuration, selects the exact
collection and binds its source, embedding descriptor, collection identity,
and content hashes. An isolated development export must still pin the active
Zotero source selectors explicitly:

```bash
export ZOTERO_NO_CLAUDE=true
export ZOTERO_MCP_CONFIG_PATH=/tmp/pkb-zotero-dev/config.json
export ZOTERO_MCP_CONFIG_DIR=/tmp/pkb-zotero-dev
export ZOTERO_LOCAL=true
export ZOTERO_LIBRARY_ID=0
export ZOTERO_LIBRARY_TYPE=user
export ZOTERO_LOCAL_PORT=23120

zotero-mcp export-snapshot \
  --config-path "$ZOTERO_MCP_CONFIG_PATH" \
  --index-path /tmp/pkb-zotero-dev/chroma-recovery-YYYYMMDD \
  --output /tmp/zotero-paper-graph-dev-snapshot.json
```

The command fails if the selected directory lacks a complete sealed manifest,
its manifest-bound publication receipt, or Chroma database. The development index must correspond to the same
development Zotero endpoint; manifest source identity plus exact key and
`dateModified` checks enforce that relationship without reading or updating
another configured library.

The output is replaced atomically. The command exits with status 2 and emits a
JSON error to stderr unless all of these checks pass:

- Zotero metadata pagination exactly matches the declared top-level item count
  and completes within the bounded page budget.
- Zotero's library version remains unchanged through the export.
- Two full reads of the existing Chroma records have identical IDs,
  documents, metadata, and vectors.
- Eligible Zotero keys and Chroma IDs match exactly. Attachments, notes, and
  annotations are excluded.
- Every Zotero `dateModified` exactly matches the indexed Chroma
  `date_modified`; missing freshness evidence fails closed.
- Every vector has 3,072 finite numeric components.
- The configured embedding model matches the model stored in the Chroma
  collection descriptor.

The `source_hash` on each embedding is SHA-256 over the exact UTF-8 bytes of
the indexed Chroma document. Creator objects are retained as supplied by
Zotero; the export does not invent or merge author identities. Retrieval time
is audit metadata and does not change the stable snapshot hash.

Paper Graph can consume the resulting file directly:

```bash
cd apps/paper-graph
uv run python -m paper_graph.storage_build \
  --source /tmp/zotero-paper-graph-snapshot.json \
  --target /tmp/paper-graph-candidate.sqlite
```

# Isolated 3072-D Index Recovery

The route contract amendment is recorded in
[`isolated-index-recovery-contract-amendment.md`](isolated-index-recovery-contract-amendment.md).

`build-index` creates a new metadata-only Chroma index without opening, resetting, or replacing the configured index. It is intended for recovery when an older collection has no authoritative embedding descriptor.

The command accepts only a path that does not exist and does not overlap the configured config or Chroma paths:

```bash
zotero-mcp build-index \
  --config-path ~/.config/zotero-mcp-dev/config.json \
  --output ~/.config/zotero-mcp-dev/chroma-recovery-YYYYMMDD \
  --collection zotero_library \
  --text-mode metadata-only
```

Before the first embedding request, the command resolves one immutable direct Gemini route and records its exact model, credential-free endpoint, `RETRIEVAL_DOCUMENT` task type, and 3072 output dimension. The isolated recovery path does not use OpenRouter because its document-task semantics cannot currently be proven. OpenRouter remains available only to the legacy embedding path. Set `ZOTERO_ISOLATED_EMBEDDING_ROUTE=gemini-direct` when an explicit choice is needed.

The source ledger is frozen once from exact Zotero top-level pagination. Attachments, notes, and annotations are excluded. Full text is not accepted because attachments do not yet have an immutable source contract.

Before embedding begins, the coordinator writes a non-secret run-state record beside the requested output. It binds the run/index IDs, frozen source identity and scope hash, eligible count, output path, and embedding descriptor, then records `complete` or `failed` without exposing provider errors. Chroma writes and validation run in separate fresh Python subprocesses. The coordinator waits for both processes to exit, checks that the Zotero library version and protected path fingerprints stayed stable, fsyncs the full staging tree, writes `manifest.json` last in staging, and publishes with atomic no-replace semantics. After the renamed directory and its parent are durable, it writes a manifest-bound `publication.json` receipt. The exporter requires both files. An interrupted staging directory or a publication that reports failure therefore remains unconsumable.

The manifest binds:

- Zotero source kind, endpoint scheme/host/port/path, library type/id, and acquisition/text/exclusion policies;
- index UUID, build run ID, Chroma collection name and ID;
- provider route, model, endpoint, document task type, and dimension;
- exact key-set, document, vector, descriptor, and collection-metadata hashes;
- Zotero-MCP, ChromaDB, google-genai, and manifest schema versions.

Export a sealed index by providing its path explicitly:

```bash
zotero-mcp export-snapshot \
  --config-path ~/.config/zotero-mcp-dev/config.json \
  --index-path ~/.config/zotero-mcp-dev/chroma-recovery-YYYYMMDD \
  --output snapshot.json
```

Export fails when `manifest.json` is absent or incomplete, the active Zotero endpoint or library differs, the current library version changed, the collection UUID/run metadata differs, or the persisted embedding descriptor no longer matches the manifest. The exporter clones the sealed directory and opens only the clone because Chroma reads can mutate its storage files.

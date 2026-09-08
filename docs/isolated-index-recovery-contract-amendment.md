# Isolated recovery route contract amendment

This amendment narrows only the embedding route portion of the approved
isolated-index recovery plan. The original plan remains preserved with SHA-256
`163290b6c1665f8194184ae67e6776c7a5a357e03b77aae35b03c9ac6f9360aa`.

An authoritative isolated build supports the `gemini-direct` route only. Its
public and persisted descriptor must identify:

- provider `gemini`;
- route `gemini-direct`;
- the exact configured model and sanitized direct Gemini endpoint;
- task type `RETRIEVAL_DOCUMENT`;
- output dimension `3072`;
- producer name and version;
- embedding descriptor schema version.

The initial model allowlist is `gemini-embedding-001` and
`gemini-embedding-2-preview`; other nonempty model names are rejected before
collection creation because the recovery contract cannot promise their
3,072-dimensional output.

Retries repeat the identical pinned direct Gemini request. They cannot change
the route, model, endpoint, task type, or dimension. OpenRouter remains part of
the legacy embedding fallback path and cannot create or export an authoritative
isolated recovery artifact because its document-task semantics are not proven.
Test factories may replace network execution, but their descriptors must still
use the production-valid direct Gemini route.

The explicit CLI config is also the source-selection authority. Local/API
mode, endpoint port, library ID/type, and any web API credential are validated
and replace ambient selectors before client construction. Publication becomes
consumable only after atomic directory publication, parent fsync, and creation
of a manifest-bound `publication.json` receipt. Interrupted staging and failed
post-rename durability checks have no valid receipt and no complete manifest.
The coordinator also keeps a non-secret run-state record outside staging from
the pre-embedding `in-progress` transition through `complete` or `failed`.
Completed manifests retain the exact Zotero-MCP, ChromaDB, and google-genai
versions, and sealed snapshot metadata preserves that producer dependency map.

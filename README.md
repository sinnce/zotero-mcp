# Zotero MCP: Chat with your Research Library—Local or Web—in Claude, ChatGPT, and more.

<p align="center">
  <a href="https://www.zotero.org/">
    <img src="https://img.shields.io/badge/Zotero-CC2936?style=for-the-badge&logo=zotero&logoColor=white" alt="Zotero">
  </a>
  <a href="https://www.anthropic.com/claude">
    <img src="https://img.shields.io/badge/Claude-6849C3?style=for-the-badge&logo=anthropic&logoColor=white" alt="Claude">
  </a>
  <a href="https://chatgpt.com/">
    <img src="https://img.shields.io/badge/ChatGPT-74AA9C?style=for-the-badge&logo=openai&logoColor=white" alt="ChatGPT">
  </a>
  <a href="https://modelcontextprotocol.io/introduction">
    <img src="https://img.shields.io/badge/MCP-0175C2?style=for-the-badge&logoColor=white" alt="MCP">
  </a>
  <a href="https://pypi.org/project/zotero-mcp-server/">
    <img src="https://img.shields.io/pypi/v/zotero-mcp-server?style=for-the-badge&logo=pypi&logoColor=white" alt="PyPI">
  </a>
</p>

**Zotero MCP** seamlessly connects your [Zotero](https://www.zotero.org/) research library with [ChatGPT](https://openai.com), [Claude](https://www.anthropic.com/claude), and other AI assistants (e.g., [Cherry Studio](https://cherry-ai.com/), [Chorus](https://chorus.sh), [Cursor](https://www.cursor.com/)) via the [Model Context Protocol](https://modelcontextprotocol.io/introduction). Review papers, get summaries, analyze citations, extract PDF annotations, and more!

---

## ✨ Features

### 🧠 AI-Powered Semantic Search
- **Vector-based similarity search** over your entire research library (requires `[semantic]` extra)
- **Multiple embedding models**: Default (free, local), OpenAI, and Gemini
- **Intelligent results** with similarity scores and contextual matching
- **Auto-updating database** with configurable sync schedules

### 🔍 Search Your Library
- Find papers, articles, and books by title, author, or content
- Perform complex searches with multiple criteria
- Browse collections, tags, and recent additions
- Semantic search for conceptual and topic-based discovery

### 📚 Access Your Content
- Retrieve detailed metadata for any item (markdown or BibTeX export)
- Get full text content (when available)
- Look up items by BetterBibTeX citation key

### 📝 Work with Annotations
- Extract and search PDF annotations with page numbers
- Access Zotero's native annotations
- Create and update notes and annotations
- Extract PDF table of contents / outlines (requires `[pdf]` extra)

### ✏️ Write Operations
- **Add papers by DOI** with auto-fetched metadata and open-access PDF cascade (Unpaywall, arXiv, Semantic Scholar, PMC)
- **Add papers by URL** (arXiv, DOI links, generic webpages) or from local files
- Create and manage collections, update item metadata, batch-update tags
- Find and merge duplicate items with dry-run preview
- **Hybrid mode**: local reads + web API writes for local-mode users

### 📊 Scite Citation Intelligence (optional `[scite]` extra)
- **Citation tallies**: See how many papers support, contrast, or mention each item — the MCP version of the [Scite Zotero Plugin](https://github.com/scitedotai/scite-zotero-plugin)
- **Retraction alerts**: Scan your library for retracted or corrected papers
- No Scite account required — uses public API endpoints

### 🌐 Flexible Access Methods
- Local mode for offline access (no API key needed)
- Web API for cloud library access
- Hybrid mode: read from local Zotero, write via web API

## 🚀 Quick Install

### Default Installation (core tools only)

The base install is lightweight — it includes search, metadata retrieval, annotations, and write operations. No ML/AI dependencies are pulled in.

#### Installing via uv (recommended)

```bash
uv tool install zotero-mcp-server
zotero-mcp setup  # Auto-configure (Claude Desktop supported)
```

#### Installing via pip

```bash
pip install zotero-mcp-server
zotero-mcp setup  # Auto-configure (Claude Desktop supported)
```

#### Installing via pipx

```bash
pipx install zotero-mcp-server
zotero-mcp setup  # Auto-configure (Claude Desktop supported)
```

### Optional Extras

Heavy ML/PDF dependencies are separated into optional extras so the base install stays fast and small:

| Extra | What it adds | Install command |
|-------|-------------|-----------------|
| `semantic` | Semantic search via ChromaDB, sentence-transformers, OpenAI/Gemini embeddings | `pip install "zotero-mcp-server[semantic]"` |
| `pdf` | PDF outline extraction (PyMuPDF) and EPUB annotation support | `pip install "zotero-mcp-server[pdf]"` |
| `docling-ocr` | Docling VLM OCR fallback for scanned/math-heavy academic PDFs via OpenAI-compatible vision APIs, with PyMuPDF PDF normalization | `pip install "zotero-mcp-server[docling-ocr]"` |
| `scite` | [Scite](https://scite.ai) citation intelligence — tallies and retraction alerts (no account needed) | `pip install "zotero-mcp-server[scite]"` |
| `all` | Everything above | `pip install "zotero-mcp-server[all]"` |

For example, with uv:
```bash
uv tool install "zotero-mcp-server[all]"    # Full install with all features
uv tool install "zotero-mcp-server[semantic]" # Just semantic search
uv tool install "zotero-mcp-server[docling-ocr]" # Docling VLM OCR fallback
```

To enable Docling OCR fallback for `extract_paper_content`, set `OPENROUTER_API_KEY` or `OPENAI_API_KEY` and add:

```json
{
  "acquisition": {
    "extraction": {
      "default_backend": "pdfminer",
      "docling_ocr_fallback": true,
      "docling_ocr_preset": "qwen",
      "docling_ocr_model": "qwen/qwen3-vl-32b-instruct",
      "docling_ocr_base_url": "https://openrouter.ai/api/v1/chat/completions",
      "docling_ocr_page_limit": 50,
      "docling_ocr_min_chars": 500,
      "docling_ocr_timeout": 120
    }
  }
}
```

If you only need basic library access (search, read, annotate, write), the default install with no extras is all you need.

#### Updating Your Installation

Keep zotero-mcp up to date with the smart update command:

```bash
# Check for updates
zotero-mcp update --check-only

# Update to latest version (preserves all configurations)
zotero-mcp update
```

## Quick Usage by Role

`zotero-mcp` is the MCP-facing backend in this workflow.

- It **does own** the MCP tool list, Zotero library access, semantic search, paper acquisition, translation-server client integration, and the resolver chain that chooses between OA and institutional access.
- It **does not own** the browser automation for session-gated downloads. When an institutional URL requires an authenticated browser session, it can call the bridge server exposed by `packages/opencode-deep-research`.
- Zotero **translation-server** is a separate HTTP service, not part of Zotero desktop and not part of this package. `zotero-mcp` only talks to it as a client.

### Local MCP server

```bash
uv run zotero-mcp serve --transport stdio
```

### Optional translation-server helper

If you want URL-to-metadata translation through Zotero translators, run translation-server separately and let `zotero-mcp` talk to it over HTTP:

```bash
# default endpoint expected by zotero-mcp
export ZOTERO_TRANSLATION_SERVER_URL=http://127.0.0.1:1969
```

Use the MCP tools `translation_server_status` and `translate_with_translation_server` to verify that service.

### Optional browser bridge for institutional PDFs

If you want `acquire_paper` to fetch through a campus proxy or other session-gated flow, start the bridge server from `packages/opencode-deep-research` and pass `session_name` to `acquire_paper`.

The ownership split is:

- `zotero-mcp`: resolve access and decide whether a session-backed location should be used
- `opencode-deep-research`: keep the named browser session alive and perform the download

Bridge auth is now fail-closed. `GET /bridge/health` carries bearer auth only. `POST /bridge/download` carries bearer auth plus the required derived allowed-domain list. If `BRIDGE_AUTH_TOKEN` is missing on the caller side, `zotero-mcp` skips bridge calls and stays on the standard direct-download path.

## 🧠 Semantic Search

Zotero MCP now includes powerful AI-powered semantic search capabilities that let you find research based on concepts and meaning, not just keywords.

### Setup Semantic Search

During setup or separately, configure semantic search:

```bash
# Configure during initial setup (recommended)
zotero-mcp setup

# Or configure semantic search separately
zotero-mcp setup --semantic-config-only
```

**Available Embedding Models:**
- **Default (all-MiniLM-L6-v2)**: Free, runs locally, good for most use cases
- **OpenAI**: Better quality, requires API key (`text-embedding-3-small` or `text-embedding-3-large`)
- **Gemini**: Better quality, requires API key (`gemini-embedding-001`)

**Update Frequency Options:**
- **Manual**: Update only when you run `zotero-mcp update-db`
- **Auto on startup**: Update database every time the server starts
- **Daily**: Update once per day automatically
- **Every N days**: Set custom interval

### Using Semantic Search

After setup, initialize your search database:

```bash
# Build the semantic search database (fast, metadata-only)
zotero-mcp update-db

# Build with full-text extraction (slower, more comprehensive)
zotero-mcp update-db --fulltext

# Use your custom zotero.sqlite path
zotero-mcp update-db --fulltext --db-path "/Your_custom_path/zotero.sqlite"

# If you have embedding conflicts or changed models, force a rebuild
zotero-mcp update-db --force-rebuild

# Check database status
zotero-mcp db-status
```

**Example Semantic Queries in your AI assistant:**
- *"Find research similar to machine learning concepts in neuroscience"*
- *"Papers that discuss climate change impacts on agriculture"*
- *"Research related to quantum computing applications"*
- *"Studies about social media influence on mental health"*
- *"Find papers conceptually similar to this abstract: [paste abstract]"*

The semantic search provides similarity scores and finds papers based on conceptual understanding, not just keyword matching.

## 🖥️ Setup & Usage

Full documentation is available at [Zotero MCP docs](https://stevenyuyy.us/zotero-mcp/).

**Requirements**
- Python 3.10+
- Zotero 7+ (for local API with full-text access)
- An MCP-compatible client (e.g., Claude Desktop, ChatGPT Developer Mode, Cherry Studio, Chorus)

**For ChatGPT setup: see the [Getting Started guide](./docs/getting-started.md).**

### For Claude Desktop (example MCP client)

#### Configuration
After installation, either:

1. **Auto-configure** (recommended):
   ```bash
   zotero-mcp setup
   ```

2. **Manual configuration**:
   Add to your `claude_desktop_config.json`:
   ```json
   {
     "mcpServers": {
       "zotero": {
         "command": "zotero-mcp",
         "env": {
           "ZOTERO_LOCAL": "true"
         }
       }
     }
   }
   ```

#### Usage

1. Start Zotero desktop (make sure local API is enabled in preferences)
2. Launch Claude Desktop
3. Access the Zotero-MCP tool through Claude Desktop's tools interface

Example prompts:
- "Search my library for papers on machine learning"
- "Find recent articles I've added about climate change"
- "Summarize the key findings from my paper on quantum computing"
- "Extract all PDF annotations from my paper on neural networks"
- "Search my notes and annotations for mentions of 'reinforcement learning'"
- "Show me papers tagged '#Arm' excluding those with '#Crypt' in my library"
- "Search for papers on operating system with tag '#Arm'"
- "Export the BibTeX citation for papers on machine learning"
- **"Find papers conceptually similar to deep learning in computer vision"** *(semantic search)*
- **"Research that relates to the intersection of AI and healthcare"** *(semantic search)*
- **"Papers that discuss topics similar to this abstract: [paste text]"** *(semantic search)*

### For Cherry Studio

#### Configuration
Go to Settings -> MCP Servers -> Edit MCP Configuration, and add the following:

```json
{
  "mcpServers": {
    "zotero": {
      "name": "zotero",
      "type": "stdio",
      "isActive": true,
      "command": "zotero-mcp",
      "args": [],
      "env": {
        "ZOTERO_LOCAL": "true"
      }
    }
  }
}
```
Then click "Save".

Cherry Studio also provides a visual configuration method for general settings and tools selection.

## 🔧 Advanced Configuration

### Using Web API Instead of Local API

For accessing your Zotero library via the web API (useful for remote setups):

```bash
zotero-mcp setup --no-local --api-key YOUR_API_KEY --library-id YOUR_LIBRARY_ID
```

### Environment Variables

**Zotero Connection:**
- `ZOTERO_LOCAL=true`: Use the local Zotero API (default: false)
- `ZOTERO_API_KEY`: Your Zotero API key (for web API)
- `ZOTERO_LIBRARY_ID`: Your Zotero library ID (for web API)
- `ZOTERO_LIBRARY_TYPE`: The type of library (user or group, default: user)

**Semantic Search:**
- `ZOTERO_EMBEDDING_MODEL`: Embedding model to use (default, openai, gemini)
- `OPENAI_API_KEY`: Your OpenAI API key (for OpenAI embeddings)
- `OPENAI_EMBEDDING_MODEL`: OpenAI model name (text-embedding-3-small, text-embedding-3-large)
- `OPENAI_BASE_URL`: Custom OpenAI endpoint URL (optional, for use with compatible APIs)
- `GEMINI_API_KEY`: Your Gemini API key (for Gemini embeddings)
- `GEMINI_EMBEDDING_MODEL`: Gemini model name (gemini-embedding-001)
- `GEMINI_BASE_URL`: Custom Gemini endpoint URL (optional, for use with compatible APIs)
- `ZOTERO_DB_PATH`: Custom `zotero.sqlite` path (optional)

### Command-Line Options

```bash
# Run the server directly
zotero-mcp serve

# Specify transport method
zotero-mcp serve --transport stdio|streamable-http|sse

# Setup and configuration
zotero-mcp setup --help                    # Get help on setup options
zotero-mcp setup --semantic-config-only    # Configure only semantic search
zotero-mcp setup-info                      # Show installation path and config info for MCP clients

# Updates and maintenance
zotero-mcp update                          # Update to latest version
zotero-mcp update --check-only             # Check for updates without installing
zotero-mcp update --force                  # Force update even if up to date

# Semantic search database management
zotero-mcp update-db                       # Update semantic search database (fast, metadata-only)
zotero-mcp update-db --fulltext             # Update with full-text extraction (comprehensive but slower)
zotero-mcp update-db --force-rebuild       # Force complete database rebuild
zotero-mcp update-db --fulltext --force-rebuild  # Rebuild with full-text extraction
zotero-mcp update-db --fulltext --db-path "your_path_to/zotero.sqlite" # Customize your zotero database path
zotero-mcp db-status                       # Show database status and info

# General
zotero-mcp version                         # Show current version
```

## 📑 PDF Annotation Extraction

Zotero MCP includes advanced PDF annotation extraction capabilities:

- **Direct PDF Processing**: Extract annotations directly from PDF files, even if they're not yet indexed by Zotero
- **Enhanced Search**: Search through PDF annotations and comments
- **Image Annotation Support**: Extract image annotations from PDFs
- **Seamless Integration**: Works alongside Zotero's native annotation system

For optimal annotation extraction, it is **highly recommended** to install the [Better BibTeX plugin](https://retorque.re/zotero-better-bibtex/installation/) for Zotero. The annotation-related functions have been primarily tested with this plugin and provide enhanced functionality when it's available.


The first time you use PDF annotation features, the necessary tools will be automatically downloaded.

## 📚 Available Tools

### 🧠 Semantic Search Tools
- `zotero_semantic_search`: AI-powered similarity search with embedding models
- `zotero_update_search_database`: Manually update the semantic search database
- `zotero_get_search_database_status`: Check database status and configuration

### 🔍 Search Tools
- `zotero_search_items`: Search your library by keywords
- `zotero_advanced_search`: Perform complex searches with multiple criteria
- `zotero_get_collections`: List collections
- `zotero_get_collection_items`: Get items in a collection
- `zotero_get_tags`: List all tags
- `zotero_get_recent`: Get recently added items
- `zotero_search_by_tag`: Search your library using custom tag filters

### 📚 Content Tools
- `zotero_get_item_metadata`: Get detailed metadata (supports BibTeX export via `format="bibtex"`)
- `zotero_get_item_fulltext`: Get full text content
- `zotero_get_item_children`: Get attachments and notes

### 📝 Annotation & Notes Tools
- `zotero_get_annotations`: Get annotations (including direct PDF extraction)
- `zotero_get_notes`: Retrieve notes from your Zotero library
- `zotero_search_notes`: Search in notes and annotations (including PDF-extracted)
- `zotero_create_note`: Create a new note for an item (beta feature)

### 📊 Scite Citation Intelligence Tools
- `scite_enrich_item`: Get Scite citation tallies and retraction alerts for a paper
- `scite_enrich_search`: Search your Zotero library with Scite-enriched results (tallies + alerts inline)
- `scite_check_retractions`: Scan items for retractions and editorial notices

### 📦 Item & Collection Management Tools
- `zotero_add_by_doi`: Add a paper by DOI with automatic metadata and open-access PDF attachment
- `zotero_add_by_url`: Add a paper by URL (arXiv, DOI URLs, and general webpages)
- `zotero_add_from_file`: Import a local PDF or EPUB file with automatic DOI extraction
- `zotero_create_collection`: Create a new collection (folder/project) in your library
- `zotero_search_collections`: Search for collections by name to find their keys
- `zotero_manage_collections`: Add or remove items from collections
- `zotero_update_item`: Update metadata for an existing item (title, tags, abstract, date, etc.)
- `zotero_find_duplicates`: Find duplicate items by title and/or DOI
- `zotero_merge_duplicates`: Merge duplicate items with dry-run preview; consolidates all child items
- `zotero_get_pdf_outline`: Extract the table of contents / outline from a PDF attachment
- `zotero_search_by_citation_key`: Look up items by BetterBibTeX citation key (with Extra field fallback)

### 🔌 Connector Compatibility Tools
- `search`: ChatGPT-compatible search wrapper for MCP connectors
- `fetch`: ChatGPT-compatible fetch wrapper for MCP connectors

## 📥 Paper Acquisition Tools (PKB-1)

These tools enable automated paper acquisition from public and institutional sources.

### New Tools
- `resolve_paper_access`: Resolve a DOI, arXiv ID, or URL to access locations using the current resolver stack
- `download_paper_artifact`: Download a paper PDF from a URL with content validation
- `extract_paper_content`: Extract text from a PDF or HTML file using the unified extraction registry
- `ingest_paper_to_zotero`: Ingest a paper (with optional PDF) into your Zotero library
- `acquire_paper`: End-to-end orchestration tool that runs resolve → download → ingest in one call. Pass a DOI, arXiv ID, or URL and get back a file path plus provenance metadata. Optionally pass `session_name` to route institutional downloads through the browser bridge (see below).

### Resolver workflow

The current source-level resolver flow is:

1. normalize the incoming DOI / arXiv ID / URL,
2. if the input is a URL, optionally ask a running Zotero translation-server for metadata and PDF attachments,
3. if the identifier becomes a DOI, try Unpaywall, then Semantic Scholar, then PMC OA,
4. if institutional access is enabled, append the institutional location,
5. if `acquire_paper` sees `requires_session=true` and you passed `session_name`, call the local bridge server before falling back to direct HTTP download.

This decision logic lives in:

- `src/zotero_mcp/acquisition/resolver.py`
- `src/zotero_mcp/acquisition/institutional.py`
- `src/zotero_mcp/tools/acquire_paper.py`

### Configuration (optional)
Add an `acquisition` section to `~/.config/zotero-mcp/config.json`:

```json
{
  "semantic_search": { },
  "acquisition": {
    "unpaywall_email": "your@email.com",
    "institutional_access": {
      "enabled": false,
      "ezproxy_prefix": "proxy.yourlib.edu"
    },
    "extraction": {
      "default_backend": "pdfminer",
      "ocr_fallback": false,
      "ocr_model": "qwen/qwen3-vl-32b-instruct",
      "openrouter_api_key": "",
      "ocr_page_limit": 50
    },
    "download": {
      "timeout_seconds": 30,
      "max_size_mb": 100
    }
  }
}
```

All fields are optional with sensible defaults. The resolver chain currently tries Unpaywall, then Semantic Scholar, then PMC OA, and finally appends any enabled institutional location. Set `unpaywall_email` for Unpaywall polite-pool access and `ncbi_email` for NCBI rate-limit compliance.

**LibProxy configuration:**

```json
{
  "acquisition": {
    "institutional_access": {
      "enabled": true,
      "provider": "libproxy",
      "libproxy_base_url": "https://libproxy.snu.ac.kr/link.n2s"
    }
  }
}
```

LibProxy redirects via query parameter (`?url=...`) and requires an authenticated browser session for access.

### Institutional / Browser-Bridge Acquisition

Some papers resolve to URLs that need an authenticated browser session, such as a campus proxy login. When `resolve_paper_access` returns a location with `requires_session: true`, `acquire_paper` can route the download through the deep-research bridge server instead of a plain HTTP fetch.

#### Operator bridge setup

Configure matching bridge auth on both sides before expecting institutional browser downloads to work. Set `BRIDGE_AUTH_TOKEN` in the caller environment and in the bridge-server environment, and keep the value private.

Operator notes:

- `BRIDGE_AUTH_TOKEN` must match on the caller and the bridge server.
- Use a private secret store, service manager secret field, or local env file that is not committed. Do not put the token in docs, screenshots, shell history snippets, or tracked config.
- Generate a token that is at least 32 characters long.
- `BRIDGE_ALLOWED_DOMAINS` is an optional caller-side policy input for extra operator-approved domains. Candidate hosts and nested redirect targets are derived automatically.
- Missing `BRIDGE_AUTH_TOKEN` on the caller disables bridge calls by design. That gives you a caller-first rollout path because direct HTTP acquisition still runs while the bridge remains opt-in.

#### Allowed-domain behavior

The caller sends an explicit allowed-domain set on `POST /bridge/download`. `GET /bridge/health` uses bearer auth only. The bridge does not auto-trust arbitrary redirects.

- Candidate hosts are derived from the requested `candidate_url`.
- For LibProxy and similar nested redirectors, hosts are also derived from nested target URLs such as the `url=` value inside the proxy URL.
- `BRIDGE_ALLOWED_DOMAINS` adds optional operator-managed extra domains after hostname normalization. Use it for known publisher CDN hosts or stable secondary download hosts that are not always visible in the initial URL.
- Redirects never expand trust automatically. If a redirected host is not in the derived or operator-approved set, the bridge rejects it instead of following it.
- Keep this list tight. Add only the domains you intend to trust for downloads.

**How it works:**

1. `acquire_paper` calls `resolve_paper_access` to find the best URL for the identifier.
2. If the resolved location requires a session and you pass `session_name`, it checks whether the bridge server is running at `http://127.0.0.1:9870` and sends bearer auth on that health check when `BRIDGE_AUTH_TOKEN` is configured.
3. If the bridge is available and the caller has a token, it sends a `POST /bridge/download` request with the DOI, candidate URL, session name, and required allowed-domain set. The bridge server handles navigation and PDF download inside the named browser session.
4. If the bridge is unavailable or `session_name` is omitted, acquisition falls back to the standard HTTP download path. Nothing breaks; you just won't get paywalled PDFs.

For LibProxy, `acquire_paper` has one extra source-backed behavior: if the DOI resolved to a LibProxy URL and you provided `session_name`, it first tries direct browser navigation to `https://doi.org/{doi}` through the browser session. If that still lands on an explicit paywall, it retries through the proxied institutional URL.

**Usage example:**

```python
# Via MCP tool call
acquire_paper(
    identifier="10.1016/j.cell.2023.01.001",
    session_name="libproxy-snu",
)
```

The session name is a logical label for a named browser profile managed by the deep-research bridge server (part of `packages/opencode-deep-research`). `"libproxy-snu"` is the default session name for SNU campus proxy access.

**Provenance:** When a download goes through the bridge, the result includes `provenance.bridge_session` set to the session name and `provenance.access_source` set to `"institutional"`. Standard HTTP downloads record only `access_source`.

**What the bridge does not do:** It does not automate SAML or Shibboleth login flows. The browser session must already be authenticated before you call `acquire_paper`. The bridge only navigates to the resolved URL and downloads the PDF.

### Translation-server is a separate service

`zotero-mcp` can talk to Zotero translation-server through `src/zotero_mcp/translation_server_client.py`, but translation-server is not the Zotero desktop binary and is not embedded in this repo.

- default endpoint: `http://127.0.0.1:1969`
- override: `ZOTERO_TRANSLATION_SERVER_URL`
- MCP tools: `translation_server_status`, `translate_with_translation_server`

When the input to `resolve_paper_access` is a URL, the resolver first checks whether translation-server is reachable. If it is, the first translated item can contribute metadata and PDF attachment URLs before the fallback URL-translation path runs.

**Bridge endpoint reference:**

| Field | Value |
|-------|-------|
| Default base URL | `http://127.0.0.1:9870` |
| Download endpoint | `POST /bridge/download` |
| Health check | `GET /bridge/health` |
| Session identifier | `session_name` string (e.g. `"libproxy-snu"`) |
| Caller auth env | `BRIDGE_AUTH_TOKEN`, same value as the bridge server |
| Caller allowlist env | `BRIDGE_ALLOWED_DOMAINS`, optional comma-separated normalized extra hosts |

### Development Setup (Dev Checkout)

Use this section when you want to run the fork directly from a local clone, for example to test new acquisition features before they're published to PyPI.

**Prerequisites:** Python 3.11+, [uv](https://docs.astral.sh/uv/)

**Clone and install:**

```bash
git clone https://github.com/your-fork/zotero-mcp /home/sinnce/zotero-mcp
cd /home/sinnce/zotero-mcp
uv sync

# Optional: install VLM OCR support (adds marker-pdf and PyTorch)
uv sync --extra ocr
```

**Configure acquisition features** in `~/.config/zotero-mcp/config.json`:

```json
{
  "semantic_search": {
    "embedding_model": "gemini"
  },
  "acquisition": {
    "unpaywall_email": "you@example.com",
    "s2_enabled": true,
    "s2_api_key": "",
    "pmc_enabled": true,
    "ncbi_email": "you@example.com",
    "auto_ingest": false,
    "institutional_access": {
      "enabled": false,
      "provider": "libproxy",
      "libproxy_base_url": "https://libproxy.snu.ac.kr/link.n2s"
    },
    "extraction": {
      "default_backend": "pdfminer",
      "ocr_fallback": false,
      "ocr_model": "qwen/qwen3-vl-32b-instruct",
      "openrouter_api_key": "",
      "ocr_page_limit": 50
    },
    "download": {
      "timeout_seconds": 30,
      "max_size_mb": 100
    }
  }
}
```

**Acquisition config fields:**

| Field | Default | Description |
|-------|---------|-------------|
| `unpaywall_email` | `""` | Email sent with Unpaywall API requests (no key needed, just an email) |
| `s2_enabled` | `true` | Enable Semantic Scholar OA resolver |
| `s2_api_key` | `""` | Optional S2 API key for higher rate limits (100 req/5min without key) |
| `pmc_enabled` | `true` | Enable PubMed Central OA resolver |
| `ncbi_email` | `""` | Recommended for NCBI API polite pool |
| `auto_ingest` | `false` | Auto-ingest acquired paper into Zotero after download |
| `extraction.ocr_fallback` | `false` | Enable VLM OCR for scanned/image PDFs (requires `[ocr]` extra and `openrouter_api_key`) |
| `extraction.ocr_model` | `"qwen/qwen3-vl-32b-instruct"` | OpenRouter model for OCR |
| `extraction.openrouter_api_key` | `""` | Required when `ocr_fallback` is true |
| `extraction.ocr_page_limit` | `50` | Max pages to process with OCR |

**Run the MCP server from the checkout:**

```bash
uv --directory /home/sinnce/zotero-mcp run zotero-mcp serve --transport stdio
```

**Verify the import works:**

```bash
cd /home/sinnce/zotero-mcp
uv run python -c "import zotero_mcp; print('import ok')"
uv run zotero-mcp version
```

**Run tests:**

```bash
cd /home/sinnce/zotero-mcp
uv run pytest tests/ -q
```

### Local setup notes and dependency discrepancies

The commands above describe the baseline checkout, but the complete test suite imports
optional semantic-search modules during test collection. A plain `uv sync` therefore
supports the core CLI and MCP server, but it does not install `chromadb`, and these
tests fail to collect:

- `tests/test_fulltext_local_mode.py`
- `tests/test_semantic_search_quality.py`
- `tests/test_semantic_stats.py`

For a development environment that can collect the semantic-search tests, install the
development extra:

```bash
uv sync --extra dev
uv run pytest tests/ -q
```

This extra currently includes the `all` extra, which pulls in ChromaDB,
sentence-transformers, PyTorch, and its transitive CUDA packages. That installation can
be substantially larger than the core runtime, especially on CPU-only machines. The
core server does not require these packages unless semantic search is used.

The local checkout used for verification imported successfully and reported
`Zotero MCP v0.2.2`. With `uv sync --extra dev`, 630 tests passed and 16 integration
tests were deselected. Two tests currently fail because
`tests/test_search_improvements.py` monkeypatches `search_module.Path`, while
`src/zotero_mcp/tools/search.py` no longer exposes `Path`; this is a stale test/source
contract rather than an installation failure.

Recommended follow-up files for keeping the setup self-consistent:

1. `pyproject.toml`: separate lightweight test dependencies from the broad `dev`
   extra, or document the intentional heavyweight dependency footprint. Also decide
   whether `all` should include the `ocr` and `docling-ocr` extras; it currently does
   not, despite the installation table describing `all` as everything above.
2. `README.md`: keep the baseline runtime setup and the full development/test setup
   as separate commands, as shown here.
3. `uv.lock`: add or refresh the lockfile if reproducible checkout environments are
   required.
4. `tests/test_search_improvements.py` and
   `src/zotero_mcp/tools/search.py`: align the `Path` test contract with the current
   implementation.

**New acquisition features in this fork:**

- **Semantic Scholar resolver** (`s2_enabled`): Queries the S2 Graph API for open-access PDFs after Unpaywall. Supports optional API key for higher rate limits.
- **PMC OA resolver** (`pmc_enabled`): Two-step lookup via NCBI ID Converter then PMC OA API. Finds PDFs for PubMed Central open-access articles.
- **VLM OCR fallback** (`ocr_fallback`): Uses marker-pdf with an OpenRouter-hosted vision model to extract text from scanned PDFs. Pure Python, no system binaries like Tesseract needed.
- **Auto-ingest** (`auto_ingest`): When enabled, `acquire_paper` automatically ingests the downloaded paper into your Zotero library and returns the item key.
- **Browser bridge**: `acquire_paper` accepts a `session_name` parameter to route institutional downloads through the deep-research bridge server (see "Institutional / Browser-Bridge Acquisition" above).

### Using this fork instead of the stable installed MCP

If you are developing in a local clone (for example `/home/sinnce/zotero-mcp`) and want your MCP client to use the fork instead of the globally installed `zotero-mcp` binary, point the client at the repo with `uv --directory ... run`.

#### Step-by-step (OpenCode / ChatGPT-style local MCP config)

1. Make sure the fork is up to date and its environment is installed:
   ```bash
   cd /home/sinnce/zotero-mcp
   uv sync
   ```
2. Update your MCP client config so the Zotero server command is repo-pinned instead of using the stable global binary.
3. For `opencode.json`, use this command array:
   ```json
   {
     "mcp": {
       "zotero": {
         "type": "local",
         "command": [
           "/home/sinnce/.local/bin/uv",
           "--directory",
           "/home/sinnce/zotero-mcp",
           "run",
           "zotero-mcp",
           "serve",
           "--transport",
           "stdio"
         ],
         "environment": {
           "ZOTERO_LOCAL": "true",
           "ZOTERO_LIBRARY_ID": "0"
         },
         "enabled": true
       }
     }
   }
   ```
4. Restart the MCP client so it drops the old stdio process and reconnects to the fork.
5. Verify the forked CLI directly before relying on the client:
   ```bash
   cd /home/sinnce/zotero-mcp
   uv run zotero-mcp version
   uv run python -m zotero_mcp.cli serve --transport stdio
   ```
   You should see the FastMCP banner and `Starting Zotero MCP server...` before the short-lived verification process exits.

### Unpaywall setup and verification (step by step)

Unpaywall does **not** use API keys. The API identifies callers by email, so the practical "registration" step is choosing the email address you want sent with each request.

#### 1) Pick the email to use with Unpaywall

- Use a real email address you control.
- No separate approval flow is required for normal API usage.
- Stay within the polite-pool guidance: approximately **100,000 requests/day per email**.

#### 2) Save that email in Zotero MCP config

Add `acquisition.unpaywall_email` to `~/.config/zotero-mcp/config.json`:

```json
{
  "semantic_search": {
    "embedding_model": "gemini"
  },
  "acquisition": {
    "unpaywall_email": "you@example.com",
    "institutional_access": {
      "enabled": false,
      "ezproxy_prefix": "proxy.yourlib.edu",
      "openurl_base": ""
    },
    "extraction": {
      "default_backend": "pdfminer",
      "ocr_fallback": false
    },
    "download": {
      "timeout_seconds": 30,
      "max_size_mb": 100
    }
  }
}
```

#### 3) Verify the email works against the live Unpaywall API

Use a known DOI and make a direct request:

```bash
curl "https://api.unpaywall.org/v2/10.1038/nature12373?email=you@example.com"
```

What to check in the JSON response:

- `is_oa: true` for an open-access example DOI
- `best_oa_location` present
- `best_oa_location.url_for_pdf` present when a direct PDF is available

#### 4) Verify the MCP path after the direct API check

After restarting your MCP client, run `resolve_paper_access` with the same DOI. A healthy result should include:

- `Identifier: doi:10.1038/nature12373`
- `Locations found: ...`
- `Best URL: ...`
- `Access method: oa`

#### 5) Verify the repo implementation before live client use

These targeted tests cover the Unpaywall client and the MCP resolve tool wrapper:

```bash
cd /home/sinnce/zotero-mcp
uv run pytest tests/test_unpaywall.py tests/test_tool_resolve.py
```

If you are also testing institutional access, the Unpaywall steps above are still enough for OA verification, but campus-proxy verification may require your institution-authenticated browser/profile.

## 🧪 Testing

### Unit Tests
```bash
uv run pytest tests/     # 294 tests, ~2 seconds
```

### Integration Test Plan
A 45-point live integration test plan is included at `docs/integration-test-plan.md`. It's designed to be given to Claude in Claude Desktop, which will execute each test against your real Zotero library. Tests cover all tools, PDF attachment cascade, attach_mode, BetterBibTeX lookups, and multi-step showcase prompts. See the file for full instructions.

## 🔍 Troubleshooting

### General Issues
- **No results found**: Ensure Zotero is running and the local API is enabled. You need to toggle on `Allow other applications on this computer to communicate with Zotero` in Zotero preferences.
- **Can't connect to library**: Check your API key and library ID if using web API
- **Full text not available**: Make sure you're using Zotero 7+ for local full-text access
- **Local library limitations**: Some functionality (tagging, library modifications) may not work with local JS API. Consider using web library setup for full functionality. (See the [docs](docs/getting-started.md#local-library-limitations) for more info.)
- **Installation/search option switching issues**: Database problems from changing install methods or search options can often be resolved with `zotero-mcp update-db --force-rebuild`

### Semantic Search Issues
- **"Missing required environment variables" when running update-db**: Run `zotero-mcp setup` to configure your environment, or the CLI will automatically load settings from your MCP client config (e.g., Claude Desktop)
- **ChromaDB / stale embedding model errors**: If you changed embedding models and see 404 errors (e.g., `text-embedding-004 is not found`), run `zotero-mcp update-db --force-rebuild` to recreate the collection with your current model. If that doesn't work, delete `~/.config/zotero-mcp/chroma_db/` and rebuild.
- **Database update takes long**: By default, `update-db` is fast (metadata-only). For comprehensive indexing with full-text, use `--fulltext` flag. Use `--limit` parameter for testing: `zotero-mcp update-db --limit 100`
- **Semantic search returns no results**: Ensure the database is initialized with `zotero-mcp update-db` and check status with `zotero-mcp db-status`
- **Limited search quality**: For better semantic search results, use `zotero-mcp update-db --fulltext` to index full-text content (requires local Zotero setup)
- **OpenAI/Gemini API errors**: Verify your API keys are correctly set and have sufficient credits/quota

### Update Issues
- **Update command fails**: Check your internet connection and try `zotero-mcp update --force`
- **Configuration lost after update**: The update process preserves configs automatically, but check `~/.config/zotero-mcp/` for backup files

## ☕ Support

If you find Zotero MCP useful, consider buying me a coffee!

<a href="https://buymeacoffee.com/stevenyuyy">
  <img src="https://img.shields.io/badge/Buy%20Me%20a%20Coffee-ffdd00?style=for-the-badge&logo=buy-me-a-coffee&logoColor=black" alt="Buy Me a Coffee">
</a>

## 📄 License

MIT

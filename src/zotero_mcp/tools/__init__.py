"""Tool modules — importing this package registers all tools with the MCP app."""

from zotero_mcp.tools import (  # noqa: F401
    annotations,
    connectors,
    retrieval,
    search,
    write,
)

# Optional: Scite enrichment (requires ``pip install zotero-mcp-server[scite]``)
try:
    from zotero_mcp.tools import scite as scite  # noqa: F401
except ImportError:
    pass

# Paper acquisition tools (PKB-1)
from zotero_mcp.tools import (  # noqa: F401
    resolve_paper_access,
    download_paper_artifact,
    extract_paper_content,
    ingest_paper_to_zotero,
)

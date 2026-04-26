from __future__ import annotations

from fastmcp import Context

from zotero_mcp._app import mcp
from zotero_mcp.translation_server_client import TranslationServerClient, TranslationServerError


@mcp.tool()
def translation_server_status(ctx: Context = None) -> dict:
    """Check whether a Zotero translation-server is reachable."""
    client = TranslationServerClient()
    return client.status().to_dict()


@mcp.tool()
def translate_with_translation_server(
    value: str,
    mode: str = "web",
    ctx: Context = None,
) -> dict:
    """Translate a URL or identifier using a running Zotero translation-server.

    mode='web' posts the input as a webpage URL to /web.
    mode='search' posts the input as an identifier/query to /search.
    """
    client = TranslationServerClient()
    status = client.status()
    if not status.available:
        return {
            "status": "unavailable",
            "base_url": client.base_url,
            "message": status.message,
        }

    try:
        if mode == "web":
            result = client.translate_web(value)
        elif mode == "search":
            result = client.translate_search(value)
        else:
            return {
                "status": "error",
                "message": f"Unsupported mode: {mode}. Use 'web' or 'search'.",
            }
    except TranslationServerError as exc:
        return {
            "status": "error",
            "base_url": client.base_url,
            "message": str(exc),
        }

    return {
        "status": "ok",
        "base_url": client.base_url,
        "mode": mode,
        "result": result,
    }

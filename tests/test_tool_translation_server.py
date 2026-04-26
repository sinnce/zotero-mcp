from unittest.mock import patch

from zotero_mcp.tools.translation_server import translate_with_translation_server, translation_server_status


def test_translation_server_status_tool_returns_status_dict():
    with patch("zotero_mcp.tools.translation_server.TranslationServerClient") as MockClient:
        instance = MockClient.return_value
        instance.status.return_value.to_dict.return_value = {
            "available": True,
            "base_url": "http://127.0.0.1:1969",
            "status_code": 200,
            "message": "ok",
        }

        result = translation_server_status()

    assert result["available"] is True


def test_translate_tool_returns_unavailable_when_server_down():
    with patch("zotero_mcp.tools.translation_server.TranslationServerClient") as MockClient:
        instance = MockClient.return_value
        instance.base_url = "http://127.0.0.1:1969"
        instance.status.return_value.available = False
        instance.status.return_value.message = "down"

        result = translate_with_translation_server("https://example.com", mode="web")

    assert result["status"] == "unavailable"


def test_translate_tool_calls_web_mode():
    with patch("zotero_mcp.tools.translation_server.TranslationServerClient") as MockClient:
        instance = MockClient.return_value
        instance.base_url = "http://127.0.0.1:1969"
        instance.status.return_value.available = True
        instance.translate_web.return_value = [{"title": "Example"}]

        result = translate_with_translation_server("https://example.com", mode="web")

    assert result["status"] == "ok"
    assert result["result"][0]["title"] == "Example"


def test_translate_tool_rejects_unknown_mode():
    with patch("zotero_mcp.tools.translation_server.TranslationServerClient") as MockClient:
        instance = MockClient.return_value
        instance.base_url = "http://127.0.0.1:1969"
        instance.status.return_value.available = True

        result = translate_with_translation_server("x", mode="bogus")

    assert result["status"] == "error"

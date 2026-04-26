from unittest.mock import patch

import requests

from zotero_mcp.translation_server_client import TranslationServerClient, TranslationServerError


class DummyResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    def json(self):
        if self._json_data is None:
            raise ValueError("no json")
        return self._json_data


def test_status_reports_available_on_non_500_response():
    client = TranslationServerClient(base_url="http://127.0.0.1:1969")

    with patch("zotero_mcp.translation_server_client.requests.get", return_value=DummyResponse(status_code=405)):
        status = client.status()

    assert status.available is True
    assert status.status_code == 405


def test_status_reports_unavailable_when_connection_fails():
    client = TranslationServerClient(base_url="http://127.0.0.1:1969")

    with patch(
        "zotero_mcp.translation_server_client.requests.get",
        side_effect=requests.RequestException("connection refused"),
    ):
        status = client.status()

    assert status.available is False


def test_translate_web_posts_plain_text_and_returns_json():
    client = TranslationServerClient(base_url="http://127.0.0.1:1969")
    payload = [{"title": "Example Item"}]

    with patch(
        "zotero_mcp.translation_server_client.requests.post",
        return_value=DummyResponse(status_code=200, json_data=payload),
    ) as mock_post:
        result = client.translate_web("https://example.com/article")

    assert result == payload
    assert mock_post.call_args.kwargs["headers"]["Content-Type"] == "text/plain"


def test_translate_search_raises_on_http_error():
    client = TranslationServerClient(base_url="http://127.0.0.1:1969")

    with patch(
        "zotero_mcp.translation_server_client.requests.post",
        return_value=DummyResponse(status_code=500, text="boom"),
    ):
        try:
            client.translate_search("10.1000/test")
        except TranslationServerError as exc:
            assert "HTTP 500" in str(exc)
        else:
            raise AssertionError("Expected TranslationServerError")

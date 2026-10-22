"""Offline checks for the measured clean_v1 port and ingestion wiring."""

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from zotero_mcp.semantic_search import ZoteroSemanticSearch
from zotero_mcp.text_clean import (
    CLEAN_VERSION,
    clean_embedded_text,
    collapse_whitespace,
    drop_rotated_watermark_runs,
    drop_running_headers,
    drop_web_chrome_lines,
    strip_html,
)

FIXTURES = Path(__file__).parent / "fixtures" / "text_clean"


def test_drop_rotated_watermark_runs():
    assert drop_rotated_watermark_runs("Start\n2\n0\n2\nv\na\nEnd") == "Start\nEnd"
    assert drop_rotated_watermark_runs("Start\na\n \nb\n\nc\nEnd") == "Start\nEnd"
    assert drop_rotated_watermark_runs("Start\na\nb\nc\nd\nEnd") == "Start\na\nb\nc\nd\nEnd"


@pytest.mark.parametrize("chrome", [
    "Change to arXiv's privacy policy", "Skip to main content", "Skip to yearly menu bar",
    "![arxiv logo](https://example.invalid/logo)", "![other](data:image/png;base64,FAKE)",
    "open search", "open navigation menu", "# [Cornell University](https://example.invalid/)",
    "arxiv logo", "main navigation", "search", "advanced search", "help", "about", "donate",
    "menu", "close search", "close navigation menu",
])
def test_drop_web_chrome_lines(chrome):
    assert drop_web_chrome_lines(f"Start\n{chrome}\nEnd") == "Start\nEnd"
    assert drop_web_chrome_lines("We search for birds and donate seeds.") == (
        "We search for birds and donate seeds."
    )


def test_strip_html():
    assert strip_html('A<br xmlns="http://www.w3.org/1999/xhtml" />B &amp; &lt;i&gt;C&lt;/i&gt;') == (
        "A B &  C "
    )


def test_drop_running_headers():
    assert drop_running_headers(
        "RESEARCH ARTICLE | SCIENCE\nFirst\n research article  | SCIENCE\nOPEN ACCESS\nLast"
    ) == "RESEARCH ARTICLE | SCIENCE\nFirst\nLast"
    assert drop_running_headers("OPEN ACCESS to science matters") == "OPEN ACCESS to science matters"


def test_collapse_whitespace():
    assert collapse_whitespace(" \tFirst\n\nsecond\r\nthird  ") == "First second third"


def test_synthetic_fixture():
    before = (FIXTURES / "arxiv_watermark_before.txt").read_text()
    after = (FIXTURES / "arxiv_watermark_after.txt").read_text().rstrip("\n")
    assert CLEAN_VERSION == "clean_v1"
    assert clean_embedded_text(before) == after


@pytest.mark.parametrize("text", [
    "", "Ordinary paragraphs.\n\nAnother paragraph.",
    "E = mc^2\nx_i + y_j = 0\n한국어 문장을 보존합니다.\nA\nB\nList ends here.",
    (FIXTURES / "arxiv_watermark_before.txt").read_text(),
    "x" * 9000,
])
def test_idempotence(text):
    cleaned = clean_embedded_text(text)
    assert clean_embedded_text(cleaned) == cleaned


def test_preserve_prose_equations_korean_and_short_lists():
    assert clean_embedded_text(
        "Normal paragraph.\n\nSecond paragraph.\nE = mc^2\nx_i + y_j = 0\n한국어 문장.\nA\nB\nList ends."
    ) == "Normal paragraph. Second paragraph. E = mc^2 x_i + y_j = 0 한국어 문장. A B List ends."


def make_search(tmp_path, clean_text=None):
    config = tmp_path / "config.json"
    extraction = {} if clean_text is None else {"clean_text": clean_text}
    config.write_text(json.dumps({"semantic_search": {"extraction": extraction}}))
    client = Mock()
    client.get_existing_ids.return_value = set()
    client.truncate_text.side_effect = lambda text: text
    with patch("zotero_mcp.semantic_search.get_zotero_client", return_value=object()):
        search = ZoteroSemanticSearch(chroma_client=client, config_path=str(config))
    client.assert_not_called()
    client.upsert_documents.assert_not_called()
    return search, client


def test_document_builder_cleans_before_character_and_token_truncation(tmp_path):
    search, client = make_search(tmp_path)
    fulltext = "Skip to main content\n" * 600 + "Body " + "z" * 9000
    item = {"key": "SYNTHETIC", "data": {
        "title": "<i>Title</i>", "abstractNote": "Abstract &amp; detail",
        "publicationTitle": "<b>Journal</b>", "tags": [{"tag": "<i>Tag</i>"}],
        "note": "<p>Note</p>", "fulltext": fulltext,
    }}
    # The original note tag stripping happens before the shared cleaner.
    expected = ("Title No authors listed Abstract & detail Journal Tag Note Body " + "z" * 9000)[:8000]
    client.truncate_text.side_effect = lambda text: text[:7900]
    stats = search._process_item_batch([item])
    assert stats["processed"] == 1
    client.truncate_text.assert_called_once_with(expected)
    docs, metadata, ids = client.upsert_documents.call_args.args
    assert docs == [expected[:7900]]
    assert metadata[0]["clean_version"] == CLEAN_VERSION
    assert ids == ["SYNTHETIC"]


@pytest.mark.parametrize("fulltext", ["", "  ", "\nOPEN ACCESS\n" + "x" * 8100],
                         ids=["empty", "whitespace", "long-fulltext"])
def test_config_off_preserves_legacy_bytes(tmp_path, fulltext):
    search, client = make_search(tmp_path, clean_text=False)
    item = {"key": "LEGACY", "data": {
        "title": "<b>Title</b>\n", "abstractNote": "Abstract &amp;  detail",
        "note": "<p>Note</p>", "fulltext": fulltext,
    }}
    structured = "<b>Title</b>\n No authors listed Abstract &amp;  detail Note"
    legacy = structured + "\n\n" + fulltext if fulltext.strip() else structured
    search._process_item_batch([item])
    client.truncate_text.assert_called_once_with(legacy)
    docs, metadata, _ = client.upsert_documents.call_args.args
    assert docs[0].encode() == legacy.encode()
    assert metadata[0]["clean_version"] == "legacy"


def test_cleaning_without_fulltext(tmp_path):
    search, client = make_search(tmp_path)
    search._process_item_batch([{"key": "STRUCTURED", "data": {"title": "<b>Title</b> &amp; more"}}])
    assert client.upsert_documents.call_args.args[0] == ["Title & more No authors listed"]


def test_reference_nested_entity_limitation():
    # Faithful parity takes precedence over making clean_v1 universally idempotent.
    once = clean_embedded_text("&amp;lt;b&amp;gt;Synthetic&amp;lt;/b&amp;gt;")
    assert once == "&lt;b&gt;Synthetic&lt;/b&gt;"
    assert clean_embedded_text(once) == "Synthetic"


def test_reference_cap_can_leave_trailing_space():
    # Trimming after the cap would change nine measured sample outputs.
    once = clean_embedded_text("x" * 7999 + " tail")
    assert once == "x" * 7999 + " "
    assert clean_embedded_text(once) == "x" * 7999

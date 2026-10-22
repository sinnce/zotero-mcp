"""Offline safety, scaling, and ingestion-policy checks for clean_v2."""

import json
import time
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


@pytest.mark.parametrize("stamp", ["arXiv:2401.12345v2", "arXiv:2401.1234"])
@pytest.mark.parametrize("reverse", [False, True])
def test_drop_rotated_watermark_runs(stamp, reverse):
    chars = stamp[::-1] if reverse else stamp
    assert drop_rotated_watermark_runs("Start\n" + "\n".join(chars) + "\nEnd") == "Start\nEnd"


@pytest.mark.parametrize("cells", ["1234567890", "abcdefghijk", "가나다라마바사아자차카"])
def test_preserve_meaningful_short_cells(cells):
    text = "Values\n" + "\n".join(cells) + "\nEnd"
    assert drop_rotated_watermark_runs(text) == text


@pytest.mark.parametrize("reverse", [False, True])
def test_watermark_keeps_adjacent_meaningful_cells(reverse):
    stamp = "arXiv:2401.12345"
    if reverse:
        stamp = stamp[::-1]
    text = "Values\n1\n2\n3\n" + "\n".join(stamp) + "\n4\n5\nEnd"
    assert drop_rotated_watermark_runs(text) == "Values\n1\n2\n3\n4\n5\nEnd"


def test_watermark_keeps_blank_and_indented_noncontributing_lines():
    text = "Start\nA\n\n" + "\n".join(" " + c for c in "arXiv:2401.12345") + "\n\nB\nEnd"
    assert drop_rotated_watermark_runs(text) == "Start\nA\n\n\nB\nEnd"


@pytest.mark.parametrize("chrome", [
    "Change to arXiv's privacy policy", "I Understand", "Skip to main content",
    "The arXiv Privacy Policy has changed. Please review the new policy.",
    "![arxiv logo](https://example.invalid/logo)",
    "![other](data:image/png;base64,FAKE)", "[logo](data:image/png,FAKE)",
    "[![arxiv logo](logo.png)](https://arxiv.org)",
    "open search", "open navigation menu",
    "The [arXiv Privacy Policy](https://info.arxiv.org/help/policies/privacy_policy.html) "
    "has changed. By continuing to use arxiv.org, you are agreeing to the privacy policy.",
])
def test_drop_web_chrome_lines(chrome):
    assert drop_web_chrome_lines(f"Start\n{chrome}\nEnd") == "Start\nEnd"


@pytest.mark.parametrize("generic", [
    "Please review our privacy policy.", "About", "Help", "arxiv logo", "main navigation",
    "search", "advanced search", "donate", "menu", "close search", "close navigation menu",
    "Skip to yearly menu bar", "# [Cornell University](https://example.invalid/)",
    "[Logo](https://example.invalid/logo.svg)",
    "* [Privacy Policy](https://arxiv.org/help/policies/privacy_policy)",
])
def test_generic_chrome_needs_nearby_distinctive_banner(generic):
    assert drop_web_chrome_lines(generic) == generic
    assert clean_embedded_text(generic) == generic
    assert drop_web_chrome_lines(f"{generic}\nI Understand\n{generic}") == ""
    distant = "I Understand\n" + "Body\n" * 5 + generic
    assert drop_web_chrome_lines(distant) == "Body\n" * 5 + generic


def test_chrome_classification_uses_original_lines_only():
    assert clean_embedded_text("Please review\nour privacy policy.") == "Please review our privacy policy."
    assert clean_embedded_text("I\nUnderstand") == "I Understand"
    assert clean_embedded_text("Help <b></b>") == "Help"
    assert drop_web_chrome_lines("![logo](logo.png)\nAbout\nHelp") == "About\nHelp"


@pytest.mark.parametrize("text", [
    "This paper evaluates a privacy policy for medical data.",
    "Title Alice Abstract: Change to arXiv's privacy policy Journal Tag",
    "References: Skip to main content (2024).",
    "We search for birds and donate seeds.",
    "Before ![arxiv logo](logo.png) after",
    "A data:image link is described here.",
    "[Privacy Policy](https://example.invalid/research)",
])
def test_chrome_substrings_preserve_prose_and_joined_fields(text):
    assert drop_web_chrome_lines(text) == text


def test_inline_data_image_preserves_surrounding_prose():
    text = "Before ![image](data:image/png;base64,FAKE) after."
    assert clean_embedded_text(text) == "Before after."


@pytest.mark.parametrize(("text", "expected"), [
    ('A<BR xmlns="url" />B &amp; C', "A B & C"),
    ("<DIV><p>Body</p></DIV>", "  Body  "),
    ("A<script>danger <b>nested</b></script>B<style>x{}</style>C", "A  B  C"),
    ("A<SCRIPT type='text/javascript'>hidden</SCRIPT>B", "A  B"),
    ("A<script>unclosed", "A<script>unclosed"),
    ("A<script>const tag = \"<b title='\";</script>B", "A  B"),
    ("0 < x < 1 and y > 2", "0 < x < 1 and y > 2"),
    ("std::vector<int>", "std::vector<int>"),
    ("a<beta>c", "a<beta>c"),
    ("a<b>c", "a<b>c"),
    ("&lt;vector&gt;", "&lt;vector&gt;"),
    ("&lt;b&gt;Literal&lt;/b&gt;", "&lt;b&gt;Literal&lt;/b&gt;"),
    ("&amp;lt;b&amp;gt;Literal", "&amp;lt;b&amp;gt;Literal"),
    ('A<span title="1 > 0">B</span>C', "A B C"),
])
def test_strip_html(text, expected):
    assert strip_html(text) == expected
    cleaned = clean_embedded_text(text)
    assert clean_embedded_text(cleaned) == cleaned


@pytest.mark.parametrize("literal", ["a<b>c", "std::vector<span>", "</span>", "<span>", "<b title='x'>"])
@pytest.mark.parametrize("markup", ["<b>Title</b>", "<span>Title</span>"])
def test_html_markup_elsewhere_does_not_consume_literal_tags(markup, literal):
    assert clean_embedded_text(markup + "\nThe notation " + literal) == "Title The notation " + literal
    assert clean_embedded_text(literal + "\n" + markup) == literal + " Title"


def test_html_pairing_is_local_to_paragraphs():
    assert strip_html("<span>literal\n\nother</span>") == "<span>literal\n\nother</span>"
    assert strip_html("<span>literal\r\n\r\nother</span>") == "<span>literal\r\n\r\nother</span>"
    assert strip_html("<span>real\nmarkup</span>") == " real\nmarkup "
    assert strip_html("<unknown>literal</unknown>") == "<unknown>literal</unknown>"
    assert strip_html("A<hr>B<img src='x'>C") == "A B C"
    source = "<span>literal\n\nother</span> " + "x" * 9000
    assert clean_embedded_text(source) == ("<span>literal other</span> " + "x" * 9000)[:8000]


def test_running_headers_require_pattern_and_three_repetitions():
    header = "RESEARCH ARTICLE | SCIENCE"
    assert drop_running_headers(f"{header}\nBody\n research article  | SCIENCE\nEnd") == (
        f"{header}\nBody\n research article  | SCIENCE\nEnd"
    )
    assert drop_running_headers(f"{header}\nBody\nresearch article | SCIENCE\n{header}") == "Body"
    assert collapse_whitespace(drop_running_headers("OPEN ACCESS\nBody\fOpen Access\nBody\fOPEN ACCESS")) == "Body Body"
    assert collapse_whitespace(drop_running_headers(
        "PNAS | vol. 1\nBody\n<!-- page break -->\nPNAS | vol. 1\nBody\n<!-- page break -->\nPNAS | vol. 1"
    )) == "Body <!-- page break --> Body <!-- page break -->"
    assert drop_running_headers("RESEARCH ARTICLE\u2003SCIENCE\n" * 3 + "Body") == "Body"


@pytest.mark.parametrize("text", [
    "Open Access\nA body heading", "OPEN ACCESS to science matters",
    "RESEARCH ARTICLE | SCIENCE\nBody", "1 | 2 | 3\n" * 4,
    "Temperature | concentration\n" * 4,
])
def test_preserve_body_headings_and_repeated_data(text):
    assert drop_running_headers(text) == text.rstrip("\n")


@pytest.mark.parametrize("row", ["Science | 12", "Open Access", "PNAS | vol. 1"])
@pytest.mark.parametrize("separator", ["\n", "\n\n"])
def test_repeated_table_cells_need_page_region_evidence(row, separator):
    text = separator.join(["Journal | count", row, row, row, "Total: 36"])
    assert drop_running_headers(text) == text
    assert clean_embedded_text(text) == collapse_whitespace(text)
    # Starting a table at the document start is not itself page evidence.
    assert drop_running_headers(separator.join([row] * 3)) == separator.join([row] * 3)


def test_page_headers_do_not_remove_identical_body_cells():
    text = "Science | 12\nBody\fScience | 12\nTable\nScience | 12\nEnd"
    assert clean_embedded_text(text) == "Body Table Science | 12 End"


def test_table_rules_are_not_page_break_evidence():
    text = "Open Access\n---\nOpen Access\n---\nOpen Access"
    assert drop_running_headers(text) == text


def test_collapse_whitespace():
    assert collapse_whitespace(" \tFirst\n\nsecond\r\nthird  ") == "First second third"


def test_synthetic_fixture():
    before = (FIXTURES / "arxiv_watermark_before.txt").read_text()
    after = (FIXTURES / "arxiv_watermark_after.txt").read_text().rstrip("\n")
    assert CLEAN_VERSION == "clean_v2"
    # The fixture's three OPEN ACCESS cells have no page-boundary evidence.
    assert clean_embedded_text(before) == after.replace("We model", "OPEN ACCESS OPEN ACCESS OPEN ACCESS We model")


@pytest.mark.parametrize("text", [
    "", "Ordinary paragraphs.\n\nAnother paragraph.",
    "E = mc^2\nx_i + y_j = 0\n한국어 문장을 보존합니다.\nA\nB\nList ends here.",
    (FIXTURES / "arxiv_watermark_before.txt").read_text(),
    "&amp;&#97;mp;", "a<&#97;>", "<i> ![x](data:image/png,x)<I Understand</style>>",
    "x" * 7998 + "<beta>", "x" * 7995 + "&amp;lt;",
    "x" * 9000, "x" * 7999 + " tail", "&amp;lt;b&amp;gt;Nested&amp;lt;/b&amp;gt;",
    "&lt;i&gt;Literal&lt;/i&gt; &amp; ordinary", "Help <b></b>",
])
def test_idempotence(text):
    cleaned = clean_embedded_text(text)
    assert clean_embedded_text(cleaned) == cleaned
    assert cleaned == cleaned.rstrip()
    assert len(cleaned) <= 8000


@pytest.mark.parametrize("kind", ["brackets", "images", "angles", "mixed"])
def test_adversarial_scaling(kind, record_property):
    timings = []
    for size in (8000, 32000, 128000):
        unit = {"brackets": "[", "images": "![", "angles": "<", "mixed": "![<["}[kind]
        text = unit * size
        samples = []
        for _ in range(3):
            start = time.perf_counter()
            clean_embedded_text(text)
            samples.append(time.perf_counter() - start)
        elapsed = min(samples)
        timings.append(elapsed)
        record_property(f"{kind}_{size}_seconds", elapsed)
        assert max(samples) < 0.5
    # Fourfold input: allow 8x plus timer/scheduler slack, reject v1's ~16x.
    assert timings[2] <= timings[1] * 8 + 0.015


def make_search(tmp_path, clean_text=None):
    config = tmp_path / "config.json"
    extraction = {} if clean_text is None else {"clean_text": clean_text}
    config.write_text(json.dumps({"semantic_search": {"extraction": extraction}}))
    client = Mock()
    client.get_existing_ids.return_value = set()
    client.truncate_text.side_effect = lambda text: text
    with patch("zotero_mcp.semantic_search.get_zotero_client", return_value=object()):
        search = ZoteroSemanticSearch(chroma_client=client, config_path=str(config))
    client.upsert_documents.assert_not_called()
    return search, client


def test_document_builder_cleans_before_character_and_token_truncation(tmp_path):
    search, client = make_search(tmp_path, clean_text=True)
    fulltext = "Skip to main content\n" * 600 + "Body " + "z" * 9000
    item = {"key": "SYNTHETIC", "data": {
        "title": "<i>Title</i>", "abstractNote": "Abstract &amp; detail",
        "publicationTitle": "<b>Journal</b>", "tags": [{"tag": "<i>Tag</i>"}],
        "note": "<p>Note</p>", "fulltext": fulltext,
    }}
    expected = ("Title No authors listed Abstract & detail Journal Tag Note Body " + "z" * 9000)[:8000]
    client.truncate_text.side_effect = lambda text: text[:7900]
    stats = search._process_item_batch([item])
    assert stats["processed"] == 1
    client.truncate_text.assert_called_once_with(expected)
    docs, metadata, ids = client.upsert_documents.call_args.args
    assert docs == [expected[:7900]]
    assert metadata[0]["clean_version"] == CLEAN_VERSION
    assert ids == ["SYNTHETIC"]


@pytest.mark.parametrize("clean_text", [False, None], ids=["explicit-off", "default-off"])
@pytest.mark.parametrize("fulltext", ["", "  ", "\nOPEN ACCESS\n" + "x" * 8100])
def test_config_off_preserves_legacy_bytes(tmp_path, fulltext, clean_text):
    search, client = make_search(tmp_path, clean_text=clean_text)
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


@pytest.mark.parametrize("old_policy", [None, "legacy", "clean_v1", CLEAN_VERSION])
@pytest.mark.parametrize("force", [False, True])
def test_api_batch_existing_records_require_deliberate_rebuild(tmp_path, old_policy, force):
    search, client = make_search(tmp_path, clean_text=True)
    client.get_existing_ids.return_value = {"OLD"}
    client.get_document_metadata.return_value = {} if old_policy is None else {"clean_version": old_policy}
    items = [
        {"key": "OLD", "data": {"title": "<b>Existing</b>"}},
        {"key": "NEW", "data": {"title": "<b>New</b>"}},
    ]
    stats = search._process_item_batch(items, force_rebuild=force)
    docs, metadata, ids = client.upsert_documents.call_args.args
    assert ids == (["OLD", "NEW"] if force else ["NEW"])
    assert stats["skipped"] == (0 if force else 1)
    assert all(m["clean_version"] == CLEAN_VERSION for m in metadata)
    assert all("<b>" not in doc for doc in docs)
    if force:
        client.get_existing_ids.assert_not_called()


def test_cleaning_without_fulltext(tmp_path):
    search, client = make_search(tmp_path, clean_text=True)
    search._process_item_batch([{"key": "STRUCTURED", "data": {"title": "<b>Title</b> &amp; more"}}])
    assert client.upsert_documents.call_args.args[0] == ["Title & more No authors listed"]


def test_cap_trims_trailing_space():
    assert clean_embedded_text("x" * 7999 + " tail") == "x" * 7999


@pytest.mark.parametrize("force", [False, True])
def test_api_update_path_does_not_migrate_existing_records(tmp_path, force):
    search, client = make_search(tmp_path, clean_text=True)
    client.get_existing_ids.return_value = {"OLD"}
    search._get_items_from_api = Mock(return_value=[
        {"key": "OLD", "data": {"title": "<b>Old</b>"}},
        {"key": "NEW", "data": {"title": "<b>New</b>"}},
    ])
    search._save_update_config = Mock()
    stats = search.update_database(force_full_rebuild=force, extract_fulltext=False)
    assert "error" not in stats
    search._get_items_from_api.assert_called_once_with(None)
    assert client.upsert_documents.call_args.args[2] == (["OLD", "NEW"] if force else ["NEW"])
    assert stats["skipped_items"] == (0 if force else 1)
    assert client.reset_collection.call_count == int(force)


def test_api_update_fails_closed_when_existing_id_lookup_fails(tmp_path):
    from zotero_mcp.chroma_client import ChromaClient

    search, client = make_search(tmp_path, clean_text=True)
    lookup = ChromaClient.__new__(ChromaClient)
    lookup.collection = Mock()
    lookup.collection.get.side_effect = RuntimeError("lookup unavailable")
    client.get_existing_ids.side_effect = lookup.get_existing_ids
    search._get_items_from_api = Mock(return_value=[{"key": "OLD", "data": {"title": "Existing"}}])
    search._save_update_config = Mock()
    stats = search.update_database(extract_fulltext=False)
    assert stats["error"] == "lookup unavailable"
    client.upsert_documents.assert_not_called()
    client.truncate_text.assert_not_called()

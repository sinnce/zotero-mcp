import hashlib
import json

import pytest

from zotero_mcp.extraction.base import ExtractionResult
from zotero_mcp.extraction.markitdown_ext import MarkItDownExtractor

FIXTURES = __import__("pathlib").Path(__file__).parent / "fixtures"
BASELINE = FIXTURES / "extraction_baseline.json"


class TestMarkItDownExtractor:
    def test_supports_html(self):
        ext = MarkItDownExtractor()
        assert ext.supports("text/html") is True

    def test_supports_xhtml(self):
        ext = MarkItDownExtractor()
        assert ext.supports("application/xhtml+xml") is True

    def test_does_not_support_pdf(self):
        ext = MarkItDownExtractor()
        assert ext.supports("application/pdf") is False

    def test_does_not_support_plain_text(self):
        ext = MarkItDownExtractor()
        assert ext.supports("text/plain") is False

    def test_extract_sample_html_returns_result(self):
        ext = MarkItDownExtractor()
        html_bytes = (FIXTURES / "sample_page.html").read_bytes()
        result = ext.extract(html_bytes, "text/html", {})
        assert isinstance(result, ExtractionResult)
        assert result.backend == "markitdown"
        assert len(result.text) > 0

    def test_extract_js_heavy_html(self):
        ext = MarkItDownExtractor()
        html_bytes = (FIXTURES / "sample_js_heavy.html").read_bytes()
        result = ext.extract(html_bytes, "text/html", {})
        assert isinstance(result, ExtractionResult)
        assert result.backend == "markitdown"

    def test_extract_empty_html_does_not_raise(self):
        ext = MarkItDownExtractor()
        result = ext.extract(b"<html><body></body></html>", "text/html", {})
        assert isinstance(result, ExtractionResult)
        assert result.backend == "markitdown"

    def test_name_attribute(self):
        ext = MarkItDownExtractor()
        assert ext.name == "markitdown"

    def test_regression_against_baseline(self):
        if not BASELINE.exists():
            pytest.skip("Baseline file not found")
        baseline = json.loads(BASELINE.read_text())
        if "sample_page_html" not in baseline:
            pytest.skip("sample_page_html not in baseline")

        ext = MarkItDownExtractor()
        html_bytes = (FIXTURES / "sample_page.html").read_bytes()
        result = ext.extract(html_bytes, "text/html", {})
        actual_hash = hashlib.sha256(result.text.encode()).hexdigest()
        expected_hash = baseline["sample_page_html"]["sha256"]
        assert actual_hash == expected_hash, (
            f"HTML regression failure.\nExpected: {expected_hash}\nActual: {actual_hash}"
        )

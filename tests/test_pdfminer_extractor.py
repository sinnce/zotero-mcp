import hashlib
import json

import pytest

from pathlib import Path

from zotero_mcp.extraction.base import ExtractionResult, ExtractorRegistry
from zotero_mcp.extraction.pdfminer_ext import PdfminerExtractor

FIXTURES = Path(__file__).parent / "fixtures"
BASELINE = FIXTURES / "extraction_baseline.json"


class TestPdfminerExtractor:
    def test_supports_pdf(self):
        assert PdfminerExtractor().supports("application/pdf") is True

    def test_does_not_support_html(self):
        assert PdfminerExtractor().supports("text/html") is False

    def test_does_not_support_plain_text(self):
        assert PdfminerExtractor().supports("text/plain") is False

    def test_name_is_pdfminer(self):
        assert PdfminerExtractor.name == "pdfminer"

    def test_extract_sample_pdf_returns_extraction_result(self):
        ext = PdfminerExtractor()
        pdf_bytes = (FIXTURES / "sample_text.pdf").read_bytes()
        result = ext.extract(pdf_bytes, "application/pdf", {})
        assert isinstance(result, ExtractionResult)
        assert result.backend == "pdfminer"

    def test_extract_sample_pdf_has_text(self):
        ext = PdfminerExtractor()
        pdf_bytes = (FIXTURES / "sample_text.pdf").read_bytes()
        result = ext.extract(pdf_bytes, "application/pdf", {})
        assert result.char_count > 0

    def test_corrupt_pdf_raises(self):
        ext = PdfminerExtractor()
        corrupt_bytes = (FIXTURES / "sample_corrupt.pdf").read_bytes()
        with pytest.raises((RuntimeError, Exception)):
            ext.extract(corrupt_bytes, "application/pdf", {})

    def test_registry_catches_corrupt_pdf_failure(self):
        reg = ExtractorRegistry()
        reg.register(PdfminerExtractor())
        corrupt_bytes = (FIXTURES / "sample_corrupt.pdf").read_bytes()
        result = reg.extract_with_fallback(corrupt_bytes, "application/pdf", {})
        assert result.quality_signal == "empty"
        assert len(result.fallback_chain) >= 1
        assert "pdfminer" in result.fallback_chain[0]


class TestExtractionRegression:
    def test_baseline_exists(self):
        assert BASELINE.exists(), f"Baseline file not found: {BASELINE}"

    def test_pdfminer_output_matches_baseline(self):
        if not BASELINE.exists():
            pytest.skip("Baseline not yet created")
        baseline = json.loads(BASELINE.read_text())
        if "sample_text_pdf" not in baseline:
            pytest.skip("sample_text_pdf not in baseline")

        expected_hash = baseline["sample_text_pdf"]["sha256"]
        ext = PdfminerExtractor()
        pdf_bytes = (FIXTURES / "sample_text.pdf").read_bytes()
        result = ext.extract(pdf_bytes, "application/pdf", {})

        actual_hash = hashlib.sha256(result.text.encode()).hexdigest()
        assert actual_hash == expected_hash, (
            f"Regression failure: output hash changed after migration.\n"
            f"Expected: {expected_hash}\nActual: {actual_hash}"
        )

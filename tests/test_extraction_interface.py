"""Tests for the extraction adapter interface and registry."""

import pytest

from zotero_mcp.extraction.base import ExtractionResult, Extractor, ExtractorRegistry, NoExtractorError


class MockPdfExtractor(Extractor):
    """Mock PDF extractor for testing."""

    name = "mock_pdf"

    def supports(self, content_type: str) -> bool:
        return content_type == "application/pdf"

    def extract(self, content: bytes, content_type: str, metadata: dict) -> "ExtractionResult":
        text = content.decode("utf-8", errors="ignore")
        return ExtractionResult(text=text, backend=self.name)


class MockDoclingExtractor(Extractor):
    name = "docling-vlm"

    def __init__(self):
        self.called = False

    def supports(self, content_type: str) -> bool:
        return content_type == "application/pdf"

    def extract(self, content: bytes, content_type: str, metadata: dict) -> "ExtractionResult":
        self.called = True
        return ExtractionResult(text="Docling OCR extracted text " * 30, backend=self.name)


class MockFailingPdfExtractor(Extractor):
    """Extractor that always fails (raises exception)."""

    name = "mock_failing_pdf"

    def supports(self, content_type: str) -> bool:
        return content_type == "application/pdf"

    def extract(self, content: bytes, content_type: str, metadata: dict) -> "ExtractionResult":
        raise ValueError("Corrupt file — cannot extract")


class MockHtmlExtractor(Extractor):
    """Mock HTML extractor for testing."""

    name = "mock_html"

    def supports(self, content_type: str) -> bool:
        return content_type == "text/html"

    def extract(self, content: bytes, content_type: str, metadata: dict) -> "ExtractionResult":
        return ExtractionResult(text="html text content " * 30, backend=self.name)


class TestExtractorABC:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            Extractor()

    def test_mock_extractor_has_name(self):
        e = MockPdfExtractor()
        assert e.name == "mock_pdf"

    def test_mock_extractor_supports(self):
        e = MockPdfExtractor()
        assert e.supports("application/pdf") is True
        assert e.supports("text/html") is False

    def test_extract_returns_extraction_result(self):
        e = MockPdfExtractor()
        result = e.extract(b"hello world", "application/pdf", {})
        assert isinstance(result, ExtractionResult)


class TestExtractorRegistry:
    def test_register_and_retrieve(self):
        reg = ExtractorRegistry()
        ext = MockPdfExtractor()
        reg.register(ext)
        retrieved = reg.get_extractor("application/pdf")
        assert retrieved is ext

    def test_no_extractor_raises(self):
        reg = ExtractorRegistry()
        with pytest.raises(NoExtractorError):
            reg.get_extractor("application/pdf")

    def test_extract_with_fallback_success(self):
        reg = ExtractorRegistry()
        reg.register(MockPdfExtractor())
        result = reg.extract_with_fallback(b"PDF content " * 50, "application/pdf", {})
        assert result.quality_signal == "good"
        assert result.backend == "mock_pdf"

    def test_extract_with_fallback_chain(self):
        """When first extractor fails, second extractor is tried."""
        reg = ExtractorRegistry()
        reg.register(MockFailingPdfExtractor())
        reg.register(MockPdfExtractor())  # Second registered, also supports PDF
        result = reg.extract_with_fallback(b"PDF content " * 50, "application/pdf", {})
        # First one failed → second succeeded
        assert result.quality_signal == "good"
        assert len(result.fallback_chain) >= 1
        assert "mock_failing_pdf" in result.fallback_chain[0]

    def test_no_cross_type_fallback(self):
        """PDF failure does NOT trigger HTML extractor."""
        reg = ExtractorRegistry()
        reg.register(MockFailingPdfExtractor())  # PDF extractor fails
        reg.register(MockHtmlExtractor())  # HTML extractor exists but should NOT be used
        result = reg.extract_with_fallback(b"some content", "application/pdf", {})
        # Should be empty — no cross-type fallback
        assert result.quality_signal == "empty"
        # The HTML extractor must NOT have been used
        assert result.backend != "mock_html"

    def test_empty_pdf_result_falls_through_to_ocr(self):
        reg = ExtractorRegistry()
        ocr = MockDoclingExtractor()
        reg.register(MockPdfExtractor())
        reg.register(ocr)

        result = reg.extract_with_fallback(b"", "application/pdf", {})

        assert result.backend == "docling-vlm"
        assert ocr.called is True
        assert result.fallback_chain == ["mock_pdf: empty", "docling-vlm: success"]

    def test_good_pdf_result_does_not_call_ocr(self):
        reg = ExtractorRegistry()
        ocr = MockDoclingExtractor()
        reg.register(MockPdfExtractor())
        reg.register(ocr)

        result = reg.extract_with_fallback(b"PDF content " * 50, "application/pdf", {})

        assert result.backend == "mock_pdf"
        assert ocr.called is False

    def test_short_pdf_result_falls_through_when_min_chars_configured(self):
        reg = ExtractorRegistry()
        reg.register(MockPdfExtractor())
        reg.register(MockDoclingExtractor())

        result = reg.extract_with_fallback(b"too short", "application/pdf", {"docling_ocr_min_chars": 500})

        assert result.backend == "docling-vlm"
        assert result.fallback_chain[0] == "mock_pdf: degraded"


class TestQualitySignal:
    def test_empty_text_is_empty(self):
        result = ExtractionResult(text="", backend="test")
        assert result.quality_signal == "empty"

    def test_short_text_is_degraded(self):
        result = ExtractionResult(text="x" * 100, backend="test")
        assert result.quality_signal == "degraded"

    def test_long_text_is_good(self):
        result = ExtractionResult(text="x" * 1000, backend="test")
        assert result.quality_signal == "good"

    def test_exactly_500_chars_is_good(self):
        result = ExtractionResult(text="x" * 500, backend="test")
        assert result.quality_signal == "good"

    def test_499_chars_is_degraded(self):
        result = ExtractionResult(text="x" * 499, backend="test")
        assert result.quality_signal == "degraded"

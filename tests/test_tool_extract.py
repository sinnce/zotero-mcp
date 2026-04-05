import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from zotero_mcp.tools.extract_paper_content import extract_paper_content
from zotero_mcp.extraction.base import ExtractionResult

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def mock_ctx():
    return MagicMock()


class TestExtractPaperContent:
    def test_pdf_extraction(self, mock_ctx):
        result = extract_paper_content(str(FIXTURES / "sample_text.pdf"), "application/pdf", mock_ctx)
        assert isinstance(result, str)
        assert "Backend" in result
        assert "pdfminer" in result

    def test_html_extraction(self, mock_ctx):
        result = extract_paper_content(str(FIXTURES / "sample_page.html"), "text/html", mock_ctx)
        assert isinstance(result, str)
        assert "Backend" in result
        assert "markitdown" in result

    def test_missing_file_returns_error(self, mock_ctx):
        result = extract_paper_content("/tmp/nonexistent_file_xyz.pdf", "application/pdf", mock_ctx)
        assert isinstance(result, str)
        assert "Error" in result or "not found" in result.lower()

    def test_quality_signal_in_response(self, mock_ctx):
        result = extract_paper_content(str(FIXTURES / "sample_text.pdf"), "application/pdf", mock_ctx)
        assert "Quality" in result
        assert any(s in result for s in ["good", "degraded", "empty"])

    def test_corrupt_pdf_empty_result(self, mock_ctx):
        result = extract_paper_content(str(FIXTURES / "sample_corrupt.pdf"), "application/pdf", mock_ctx)
        assert isinstance(result, str)
        assert "empty" in result or "fallback" in result.lower() or "Error" in result

    def test_returns_string(self, mock_ctx):
        result = extract_paper_content(str(FIXTURES / "sample_text.pdf"), "application/pdf", mock_ctx)
        assert isinstance(result, str)

"""Tests for HTTP client utilities."""

import pytest
import httpx
from pytest_httpx import HTTPXMock
from zotero_mcp.acquisition.http_client import HttpResult, fetch_with_retry, validate_pdf_bytes, validate_content_type


class TestPdfValidation:
    def test_valid_pdf_magic_bytes(self):
        assert validate_pdf_bytes(b"%PDF-1.4 content here") is True

    def test_html_content_not_pdf(self):
        assert validate_pdf_bytes(b"<html><body>Not a PDF</body></html>") is False

    def test_empty_content_not_pdf(self):
        assert validate_pdf_bytes(b"") is False

    def test_partial_pdf_header(self):
        assert validate_pdf_bytes(b"%PD") is False  # Too short


class TestContentTypeValidation:
    def test_pdf_content_type(self):
        assert validate_content_type("application/pdf", "application/pdf") is True

    def test_wrong_content_type(self):
        assert validate_content_type("text/html", "application/pdf") is False

    def test_content_type_with_charset(self):
        # application/pdf; charset=utf-8 should still match
        assert validate_content_type("application/pdf; charset=utf-8", "application/pdf") is True


@pytest.mark.asyncio
class TestFetchWithRetry:
    async def test_successful_fetch(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url="https://example.com/paper.pdf",
            content=b"%PDF-1.4 test content",
            headers={"content-type": "application/pdf"},
        )
        result = await fetch_with_retry("https://example.com/paper.pdf")
        assert result.success is True
        assert result.content == b"%PDF-1.4 test content"
        assert result.status_code == 200

    async def test_retry_on_429(self, httpx_mock: HTTPXMock):
        # First two requests fail with 429, third succeeds
        httpx_mock.add_response(status_code=429)
        httpx_mock.add_response(status_code=429)
        httpx_mock.add_response(content=b"%PDF-1.4", headers={"content-type": "application/pdf"})
        result = await fetch_with_retry(
            "https://example.com/paper.pdf",
            max_retries=3,
            retry_delay=0.0,  # No delay in tests
        )
        assert result.success is True

    async def test_timeout_returns_error_result(self, httpx_mock: HTTPXMock):
        httpx_mock.add_exception(httpx.TimeoutException("timeout"))
        result = await fetch_with_retry("https://example.com/paper.pdf", max_retries=1, retry_delay=0.0)
        assert result.success is False
        assert result.error_message is not None

    async def test_never_raises(self, httpx_mock: HTTPXMock):
        """fetch_with_retry must always return HttpResult, never raise."""
        httpx_mock.add_exception(Exception("network error"))
        result = await fetch_with_retry("https://example.com/paper.pdf", max_retries=1, retry_delay=0.0)
        assert isinstance(result, HttpResult)
        assert result.success is False

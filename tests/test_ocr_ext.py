from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

from zotero_mcp.extraction.base import ExtractionResult
from zotero_mcp.acquisition.config import AcquisitionConfig, ExtractionConfig


def _make_marker_modules(rendered_text: str = "Extracted OCR text from PDF") -> dict:
    mock_rendered = MagicMock()
    mock_rendered.markdown = rendered_text

    mock_converter_instance = MagicMock()
    mock_converter_instance.return_value = mock_rendered

    mock_converter_cls = MagicMock(return_value=mock_converter_instance)
    mock_service_cls = MagicMock()
    mock_model_dict = MagicMock(return_value={})

    marker_mod = ModuleType("marker")
    marker_converters = ModuleType("marker.converters")
    marker_converters_pdf = ModuleType("marker.converters.pdf")
    marker_converters_pdf.PdfConverter = mock_converter_cls
    marker_models = ModuleType("marker.models")
    marker_models.create_model_dict = mock_model_dict
    marker_services = ModuleType("marker.services")
    marker_services_openai = ModuleType("marker.services.openai")
    marker_services_openai.OpenAIService = mock_service_cls

    return {
        "marker": marker_mod,
        "marker.converters": marker_converters,
        "marker.converters.pdf": marker_converters_pdf,
        "marker.models": marker_models,
        "marker.services": marker_services,
        "marker.services.openai": marker_services_openai,
    }


def _make_config(ocr_fallback: bool = True, api_key: str = "test-key") -> AcquisitionConfig:
    extraction = ExtractionConfig(
        default_backend="pdfminer",
        ocr_fallback=ocr_fallback,
        ocr_model="qwen/qwen3-vl-32b-instruct",
        openrouter_api_key=api_key,
        ocr_page_limit=50,
    )
    config = AcquisitionConfig()
    config.extraction = extraction
    return config


PDF_BYTES = b"%PDF-1.4 fake pdf content"


class TestMarkerVLMExtractor:
    def test_vlm_ocr_extract(self):
        fake_modules = _make_marker_modules("Hello from VLM OCR")
        config = _make_config(ocr_fallback=True, api_key="sk-test-123")

        with patch.dict(sys.modules, fake_modules):
            from zotero_mcp.extraction.ocr_ext import MarkerVLMExtractor

            extractor = MarkerVLMExtractor(config=config)

            assert extractor.supports("application/pdf") is True

            result = extractor.extract(PDF_BYTES, "application/pdf", {})

        assert isinstance(result, ExtractionResult)
        assert result.backend == "marker-vlm"
        assert result.text == "Hello from VLM OCR"
        assert result.char_count > 0
        assert result.quality_signal in ("good", "degraded")

    def test_no_api_key(self):
        fake_modules = _make_marker_modules()
        config_no_key = _make_config(ocr_fallback=True, api_key="")

        with patch.dict(sys.modules, fake_modules):
            from zotero_mcp.extraction.ocr_ext import MarkerVLMExtractor

            extractor = MarkerVLMExtractor(config=config_no_key)
            assert extractor.supports("application/pdf") is False

    def test_no_api_key_env(self, monkeypatch):
        fake_modules = _make_marker_modules()
        config_no_key = _make_config(ocr_fallback=True, api_key="")
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

        with patch.dict(sys.modules, fake_modules):
            from zotero_mcp.extraction.ocr_ext import MarkerVLMExtractor

            extractor = MarkerVLMExtractor(config=config_no_key)
            assert extractor.supports("application/pdf") is False

    def test_api_error_never_raises(self):
        fake_modules = _make_marker_modules()
        mock_converter_cls = MagicMock(side_effect=RuntimeError("API quota exceeded"))
        fake_modules["marker.converters.pdf"].PdfConverter = mock_converter_cls

        config = _make_config(ocr_fallback=True, api_key="sk-test-456")

        with patch.dict(sys.modules, fake_modules):
            from zotero_mcp.extraction.ocr_ext import MarkerVLMExtractor

            extractor = MarkerVLMExtractor(config=config)
            result = extractor.extract(PDF_BYTES, "application/pdf", {})

        assert isinstance(result, ExtractionResult)
        assert result.backend == "marker-vlm"
        assert result.text == ""
        assert result.quality_signal == "empty"

    def test_ocr_fallback_false(self):
        fake_modules = _make_marker_modules()
        config = _make_config(ocr_fallback=False, api_key="sk-test-789")

        with patch.dict(sys.modules, fake_modules):
            from zotero_mcp.extraction.ocr_ext import MarkerVLMExtractor

            extractor = MarkerVLMExtractor(config=config)
            assert extractor.supports("application/pdf") is False

    def test_marker_not_installed(self):
        with patch.dict(sys.modules, {"marker": None}):
            from zotero_mcp.extraction.ocr_ext import MarkerVLMExtractor

            config = _make_config(ocr_fallback=True, api_key="sk-test-abc")
            extractor = MarkerVLMExtractor(config=config)
            assert extractor.supports("application/pdf") is False

    def test_non_pdf_not_supported(self):
        fake_modules = _make_marker_modules()
        config = _make_config(ocr_fallback=True, api_key="sk-test-def")

        with patch.dict(sys.modules, fake_modules):
            from zotero_mcp.extraction.ocr_ext import MarkerVLMExtractor

            extractor = MarkerVLMExtractor(config=config)
            assert extractor.supports("text/html") is False
            assert extractor.supports("application/epub+zip") is False

    def test_page_limit(self):
        rendered_text = "Page-limited OCR output"
        fake_modules = _make_marker_modules(rendered_text)
        config = _make_config(ocr_fallback=True, api_key="sk-test-ghi")
        config.extraction.ocr_page_limit = 5

        with patch.dict(sys.modules, fake_modules):
            from zotero_mcp.extraction.ocr_ext import MarkerVLMExtractor

            extractor = MarkerVLMExtractor(config=config)
            result = extractor.extract(PDF_BYTES, "application/pdf", {})

        assert result.text == rendered_text
        converter_cls = fake_modules["marker.converters.pdf"].PdfConverter
        call_kwargs = converter_cls.call_args
        assert call_kwargs is not None
        config_arg = call_kwargs.kwargs.get("config") or {}
        if isinstance(config_arg, dict):
            assert config_arg.get("max_pages") == 5

    def test_name_property(self):
        with patch.dict(sys.modules, _make_marker_modules()):
            from zotero_mcp.extraction.ocr_ext import MarkerVLMExtractor

            extractor = MarkerVLMExtractor(config=_make_config())
            assert extractor.name == "marker-vlm"

    def test_env_api_key_fallback(self, monkeypatch):
        fake_modules = _make_marker_modules()
        config_no_key = _make_config(ocr_fallback=True, api_key="")
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-env-key-123")

        with patch.dict(sys.modules, fake_modules):
            from zotero_mcp.extraction.ocr_ext import MarkerVLMExtractor

            extractor = MarkerVLMExtractor(config=config_no_key)
            assert extractor.supports("application/pdf") is True

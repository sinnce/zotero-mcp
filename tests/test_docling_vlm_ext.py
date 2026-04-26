from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

from zotero_mcp.acquisition.config import AcquisitionConfig, ExtractionConfig
from zotero_mcp.extraction.base import ExtractionResult

PDF_BYTES = b"%PDF-1.4 fake pdf content"


def _make_config(enabled: bool = True, api_key: str = "sk-test") -> AcquisitionConfig:
    cfg = AcquisitionConfig()
    cfg.extraction = ExtractionConfig(
        docling_ocr_fallback=enabled,
        docling_ocr_preset="qwen",
        docling_ocr_model="qwen/test-vl",
        docling_ocr_base_url="https://openrouter.ai/api/v1/chat/completions",
        docling_ocr_api_key=api_key,
        docling_ocr_page_limit=2,
        docling_ocr_min_chars=500,
        docling_ocr_timeout=7,
    )
    return cfg


def _make_docling_modules(markdown: str = "# OCR Markdown\n\n| A | B |\n|---|---|\n| 1 | 2 |") -> dict:
    document = MagicMock()
    document.export_to_markdown.return_value = markdown
    convert_result = MagicMock()
    convert_result.document = document

    converter_instance = MagicMock()
    converter_instance.convert.return_value = convert_result

    docling = ModuleType("docling")
    base_models = ModuleType("docling.datamodel.base_models")
    base_models.InputFormat = MagicMock(PDF="PDF")
    pipeline_options = ModuleType("docling.datamodel.pipeline_options")
    pipeline_options.VlmConvertOptions = MagicMock()
    pipeline_options.VlmConvertOptions.from_preset.return_value = MagicMock()
    pipeline_options.VlmPipelineOptions = MagicMock()
    vlm_engine_options = ModuleType("docling.datamodel.vlm_engine_options")
    vlm_engine_options.ApiVlmEngineOptions = MagicMock()
    vlm_engine_options.VlmEngineType = MagicMock(API="API")
    document_converter = ModuleType("docling.document_converter")
    document_converter.DocumentConverter = MagicMock(return_value=converter_instance)
    document_converter.PdfFormatOption = MagicMock()
    vlm_pipeline = ModuleType("docling.pipeline.vlm_pipeline")
    vlm_pipeline.VlmPipeline = MagicMock()

    return {
        "docling": docling,
        "docling.datamodel": ModuleType("docling.datamodel"),
        "docling.datamodel.base_models": base_models,
        "docling.datamodel.pipeline_options": pipeline_options,
        "docling.datamodel.vlm_engine_options": vlm_engine_options,
        "docling.document_converter": document_converter,
        "docling.pipeline": ModuleType("docling.pipeline"),
        "docling.pipeline.vlm_pipeline": vlm_pipeline,
    }


class TestDoclingVLMExtractor:
    def test_supports_false_when_config_disabled(self):
        with patch.dict(sys.modules, _make_docling_modules()):
            from zotero_mcp.extraction.docling_vlm_ext import DoclingVLMExtractor

            assert DoclingVLMExtractor(_make_config(enabled=False)).supports("application/pdf") is False

    def test_supports_false_without_api_key(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with patch.dict(sys.modules, _make_docling_modules()):
            from zotero_mcp.extraction.docling_vlm_ext import DoclingVLMExtractor

            assert DoclingVLMExtractor(_make_config(api_key="")).supports("application/pdf") is False

    def test_supports_false_when_docling_missing(self):
        with patch.dict(sys.modules, {"docling": None}):
            from zotero_mcp.extraction.docling_vlm_ext import DoclingVLMExtractor

            assert DoclingVLMExtractor(_make_config()).supports("application/pdf") is False

    def test_supports_false_for_non_pdf(self):
        with patch.dict(sys.modules, _make_docling_modules()):
            from zotero_mcp.extraction.docling_vlm_ext import DoclingVLMExtractor

            assert DoclingVLMExtractor(_make_config()).supports("text/html") is False

    def test_successful_extraction_returns_markdown(self):
        fake_modules = _make_docling_modules("Equation: $E=mc^2$\n\n| x | y |")
        with patch.dict(sys.modules, fake_modules):
            from zotero_mcp.extraction.docling_vlm_ext import DoclingVLMExtractor

            result = DoclingVLMExtractor(_make_config()).extract(PDF_BYTES, "application/pdf", {})

        assert isinstance(result, ExtractionResult)
        assert result.backend == "docling-vlm"
        assert "$E=mc^2$" in result.text

        converter = fake_modules["docling.document_converter"].DocumentConverter.return_value
        assert converter.convert.call_args.kwargs["max_num_pages"] == 2

    def test_converter_failure_returns_empty_result(self):
        fake_modules = _make_docling_modules()
        fake_modules["docling.document_converter"].DocumentConverter.side_effect = RuntimeError("API failed")
        with patch.dict(sys.modules, fake_modules):
            from zotero_mcp.extraction.docling_vlm_ext import DoclingVLMExtractor

            result = DoclingVLMExtractor(_make_config()).extract(PDF_BYTES, "application/pdf", {})

        assert result.backend == "docling-vlm"
        assert result.text == ""
        assert result.quality_signal == "empty"

    def test_temp_file_cleanup(self):
        fake_modules = _make_docling_modules()
        with patch.dict(sys.modules, fake_modules), patch("os.unlink") as unlink:
            from zotero_mcp.extraction.docling_vlm_ext import DoclingVLMExtractor

            DoclingVLMExtractor(_make_config()).extract(PDF_BYTES, "application/pdf", {})

        assert unlink.call_count >= 1

    def test_env_api_key_fallback_order(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
        with patch.dict(sys.modules, _make_docling_modules()):
            from zotero_mcp.extraction.docling_vlm_ext import DoclingVLMExtractor

            assert DoclingVLMExtractor(_make_config(api_key="")).supports("application/pdf") is True

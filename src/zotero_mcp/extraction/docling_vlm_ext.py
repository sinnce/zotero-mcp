from __future__ import annotations

import gc
import logging
import os
import tempfile

from .base import ExtractionResult, Extractor

logger = logging.getLogger(__name__)


def _docling_available() -> bool:
    try:
        import docling  # type: ignore[import-not-found] # noqa: F401

        return True
    except Exception:
        return False


class DoclingVLMExtractor(Extractor):
    name = "docling-vlm"

    def __init__(self, config=None):
        self.config = config
        extraction = getattr(config, "extraction", None)
        self._api_key = (
            getattr(extraction, "docling_ocr_api_key", "")
            or os.environ.get("OPENROUTER_API_KEY", "")
            or os.environ.get("OPENAI_API_KEY", "")
        )
        self._preset = getattr(extraction, "docling_ocr_preset", "qwen") or "qwen"
        self._model = getattr(extraction, "docling_ocr_model", "") or "qwen/qwen3-vl-32b-instruct"
        self._base_url = (
            getattr(extraction, "docling_ocr_base_url", "") or "https://openrouter.ai/api/v1/chat/completions"
        )
        self._page_limit = getattr(extraction, "docling_ocr_page_limit", 50) or 50
        self._timeout = getattr(extraction, "docling_ocr_timeout", 120) or 120

    def supports(self, content_type: str) -> bool:
        if content_type != "application/pdf":
            return False
        if not self._api_key:
            return False
        if not _docling_available():
            return False
        extraction = getattr(self.config, "extraction", None)
        return bool(getattr(extraction, "docling_ocr_fallback", False))

    def extract(self, content: bytes, content_type: str, metadata: dict) -> ExtractionResult:
        tmp_path: str | None = None
        normalized_path: str | None = None
        converter = None
        result = None
        try:
            from docling.datamodel.base_models import InputFormat  # type: ignore[import-not-found]
            from docling.datamodel.pipeline_options import (  # type: ignore[import-not-found]
                VlmConvertOptions,
                VlmPipelineOptions,
            )
            from docling.datamodel.vlm_engine_options import (  # type: ignore[import-not-found]
                ApiVlmEngineOptions,
                VlmEngineType,
            )
            from docling.document_converter import DocumentConverter, PdfFormatOption  # type: ignore[import-not-found]
            from docling.pipeline.vlm_pipeline import VlmPipeline  # type: ignore[import-not-found]

            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(content)
                tmp_path = tmp.name

            params: dict[str, object] = {"temperature": 0.0}
            if self._model:
                params["model"] = self._model

            vlm_options = VlmConvertOptions.from_preset(
                self._preset,
                engine_options=ApiVlmEngineOptions(
                    runtime_type=VlmEngineType.API,
                    url=self._base_url,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    params=params,
                    timeout=self._timeout,
                ),
            )
            pipeline_options = VlmPipelineOptions(
                vlm_options=vlm_options,
                enable_remote_services=True,
            )
            converter = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(
                        pipeline_options=pipeline_options,
                        pipeline_cls=VlmPipeline,
                    )
                }
            )
            try:
                result = converter.convert(tmp_path, max_num_pages=self._page_limit)
            except Exception as exc:
                if "not valid" not in str(exc).lower():
                    raise
                normalized_path = self._normalize_pdf(content)
                result = converter.convert(normalized_path, max_num_pages=self._page_limit)
            markdown = result.document.export_to_markdown() if result and result.document else ""
            return ExtractionResult(text=markdown or "", backend=self.name)
        except Exception as exc:
            logger.warning("DoclingVLMExtractor failed: %s", exc)
            return ExtractionResult(text="", backend=self.name)
        finally:
            if tmp_path is not None:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            if normalized_path is not None:
                try:
                    os.unlink(normalized_path)
                except OSError:
                    pass
            # Docling's VLM objects have destructors that may touch module globals during
            # interpreter shutdown. Releasing them here avoids noisy ignored exceptions.
            del result
            del converter
            gc.collect()

    def _normalize_pdf(self, content: bytes) -> str:
        import fitz  # type: ignore[import-not-found]

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as src:
            src.write(content)
            src_path = src.name

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as dst:
            dst_path = dst.name

        try:
            source_doc = fitz.open(src_path)
            normalized_doc = fitz.open()
            page_limit = min(self._page_limit, len(source_doc))
            if page_limit > 0:
                normalized_doc.insert_pdf(source_doc, from_page=0, to_page=page_limit - 1)
            normalized_doc.save(dst_path, garbage=4, deflate=True, clean=True)
            normalized_doc.close()
            source_doc.close()
            return dst_path
        finally:
            try:
                os.unlink(src_path)
            except OSError:
                pass

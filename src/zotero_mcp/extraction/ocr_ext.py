from __future__ import annotations

import logging
import os
import tempfile

from .base import Extractor, ExtractionResult

logger = logging.getLogger(__name__)


def _marker_available() -> bool:
    try:
        import marker  # noqa: F401

        return True
    except (ImportError, Exception):
        return False


class MarkerVLMExtractor(Extractor):
    name = "marker-vlm"

    def __init__(self, config=None):
        self.config = config
        self._api_key = getattr(getattr(config, "extraction", None), "openrouter_api_key", None) or os.environ.get(
            "OPENROUTER_API_KEY", ""
        )
        self._ocr_model = (
            getattr(getattr(config, "extraction", None), "ocr_model", None) or "qwen/qwen3-vl-32b-instruct"
        )
        self._page_limit = getattr(getattr(config, "extraction", None), "ocr_page_limit", None) or 50

    def supports(self, content_type: str) -> bool:
        if content_type != "application/pdf":
            return False
        if not self._api_key:
            return False
        if not _marker_available():
            return False
        ocr_fallback = getattr(getattr(self.config, "extraction", None), "ocr_fallback", False)
        return bool(ocr_fallback)

    def extract(self, content: bytes, content_type: str, metadata: dict) -> ExtractionResult:
        try:
            from marker.converters.pdf import PdfConverter
            from marker.models import create_model_dict
            from marker.services.openai import OpenAIService

            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(content)
                tmp_path = tmp.name

            try:
                service = OpenAIService(
                    openai_api_key=self._api_key,
                    openai_base_url="https://openrouter.ai/api/v1",
                    openai_model=self._ocr_model,
                )
                converter = PdfConverter(
                    artifact_dict=create_model_dict(),
                    config={"use_llm": True, "max_pages": self._page_limit},
                    llm_service=service,
                )
                rendered = converter(tmp_path)
                text = ""
                if hasattr(rendered, "markdown"):
                    text = rendered.markdown
                elif hasattr(rendered, "text"):
                    text = rendered.text
                return ExtractionResult(text=text, backend="marker-vlm")
            finally:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

        except Exception as exc:
            logger.warning("MarkerVLMExtractor failed: %s", exc)
            return ExtractionResult(text="", backend="marker-vlm")

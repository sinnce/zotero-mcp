"""MarkItDown + BeautifulSoup extractor for HTML content."""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from .base import Extractor, ExtractionResult

logger = logging.getLogger(__name__)


class MarkItDownExtractor(Extractor):
    """Extract text from HTML using MarkItDown with BeautifulSoup internal fallback.

    The MarkItDown → BeautifulSoup fallback is an INTERNAL fallback within this
    extractor, not a registry-level cross-type fallback. The registry treats this
    as a single extractor entry.
    """

    name = "markitdown"

    def supports(self, content_type: str) -> bool:
        return content_type in ("text/html", "application/xhtml+xml")

    def extract(self, content: bytes, content_type: str, metadata: dict) -> ExtractionResult:
        """Extract text from HTML content.

        Internal fallback order: MarkItDown → BeautifulSoup.
        Does NOT raise; returns a degraded/empty result on total failure.
        """
        html_text = content.decode("utf-8", errors="ignore")
        fallback_chain: list[str] = []

        tmp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8") as tmp:
                tmp.write(html_text)
                tmp_path = tmp.name
            try:
                from markitdown import MarkItDown

                md = MarkItDown()
                result = md.convert(tmp_path)
                text = result.text_content or ""
                if text.strip():
                    return ExtractionResult(text=text, backend=self.name)
                fallback_chain.append("markitdown: returned empty text")
            except Exception as e:
                fallback_chain.append(f"markitdown: {type(e).__name__}({e})")
        except Exception as e:
            fallback_chain.append(f"markitdown: temp file setup failed: {e}")
        finally:
            if tmp_path is not None:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

        try:
            from bs4 import BeautifulSoup  # type: ignore

            text = BeautifulSoup(html_text, "html.parser").get_text(" ")
            result = ExtractionResult(text=text, backend=self.name)
            result.fallback_chain = fallback_chain
            return result
        except Exception as e:
            return ExtractionResult(
                text="",
                backend=self.name,
                fallback_chain=fallback_chain + [f"beautifulsoup: {e}"],
            )

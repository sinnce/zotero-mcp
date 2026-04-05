"""pdfminer extractor — wraps the subprocess-based PDF extraction from local_db.py.

CRITICAL: Uses subprocess to avoid FastMCP server initialization deadlock.
See local_db.py:_extract_text_from_pdf for the original implementation and the
full explanation of why subprocess.run is used instead of a direct pdfminer import.

See: https://github.com/54yyyu/zotero-mcp/issues/178
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from .base import Extractor, ExtractionResult

logger = logging.getLogger(__name__)

# Exact same inline script as local_db.py — do NOT change without updating local_db.py.
# Imports ONLY pdfminer, never zotero_mcp, so the child process cannot trigger
# FastMCP server initialization.
_PDFMINER_SCRIPT = (
    "import sys, logging; "
    "logging.getLogger('pdfminer').setLevel(logging.ERROR); "
    "from pdfminer.high_level import extract_text; "
    "sys.stdout.write(extract_text(sys.argv[1], maxpages=int(sys.argv[2])) or '')"
)


class PdfminerExtractor(Extractor):
    """Extract text from PDFs using pdfminer in a subprocess (avoids FastMCP deadlock)."""

    name = "pdfminer"

    def __init__(self, max_pages: int = 10, timeout: float = 30.0) -> None:
        self._max_pages = max_pages
        self._timeout = timeout

    def supports(self, content_type: str) -> bool:
        return content_type == "application/pdf"

    def extract(self, content: bytes, content_type: str, metadata: dict) -> ExtractionResult:
        """Extract text from PDF bytes via a subprocess.

        Writes *content* to a temporary file and runs pdfminer in a child
        Python process so that zotero_mcp is never imported in the child,
        avoiding the FastMCP initialization deadlock.

        Returns:
            ExtractionResult with the extracted text (may be empty for
            image-only / scanned PDFs — that is not an error).

        Raises:
            RuntimeError: if the subprocess exits with a non-zero status
                          or if the timeout expires.  The registry catches
                          RuntimeError and records it in fallback_chain.
        """
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            result = subprocess.run(
                [sys.executable, "-c", _PDFMINER_SCRIPT, tmp_path, str(self._max_pages)],
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"pdfminer subprocess exited with code {result.returncode}: "
                    f"{result.stderr[:200] if result.stderr else 'no stderr'}"
                )
            text = result.stdout or ""
            return ExtractionResult(text=text, backend=self.name)

        except subprocess.TimeoutExpired:
            raise RuntimeError(f"pdfminer subprocess timed out after {self._timeout}s")

        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

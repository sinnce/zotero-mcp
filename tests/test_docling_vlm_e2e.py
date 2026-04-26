from __future__ import annotations

import os
import tarfile
import urllib.request
from pathlib import Path
from typing import cast

import pytest

from zotero_mcp.acquisition.config import AcquisitionConfig, ExtractionConfig
from zotero_mcp.extraction.docling_vlm_ext import DoclingVLMExtractor

ARXIV_ID = "1706.03762"
PDF_URL = f"https://arxiv.org/pdf/{ARXIV_ID}"
SOURCE_URL = f"https://arxiv.org/e-print/{ARXIV_ID}"


def _download(url: str, dest: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "zotero-mcp-docling-ocr-e2e/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        dest.write_bytes(response.read())


@pytest.mark.integration
def test_docling_vlm_arxiv_math_and_table_e2e(tmp_path):
    api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        pytest.skip("OPENROUTER_API_KEY or OPENAI_API_KEY is required for live Docling VLM OCR")
    api_key = cast(str, api_key)

    pdf_path = tmp_path / f"{ARXIV_ID}.pdf"
    source_path = tmp_path / f"{ARXIV_ID}-source.tar.gz"
    source_dir = tmp_path / "source"
    source_dir.mkdir()

    _download(PDF_URL, pdf_path)
    _download(SOURCE_URL, source_path)
    with tarfile.open(source_path, "r:gz") as archive:
        archive.extractall(source_dir)

    model_architecture = (source_dir / "model_architecture.tex").read_text()
    results = (source_dir / "results.tex").read_text()
    assert r"\mathrm{Attention}(Q, K, V)" in model_architecture
    assert "Transformer (base model)" in results
    assert "BLEU" in results

    import fitz  # type: ignore[import-not-found]

    eval_pdf_path = tmp_path / f"{ARXIV_ID}-math-table-pages.pdf"
    source_pdf = fitz.open(pdf_path)
    eval_pdf = fitz.open()
    for page_index in (3, 7, 8):
        eval_pdf.insert_pdf(source_pdf, from_page=page_index, to_page=page_index)
    eval_pdf.save(eval_pdf_path, garbage=4, deflate=True, clean=True)
    eval_pdf.close()
    source_pdf.close()

    cfg = AcquisitionConfig()
    cfg.extraction = ExtractionConfig(
        docling_ocr_fallback=True,
        docling_ocr_preset=os.environ.get("DOCLING_OCR_PRESET", "qwen"),
        docling_ocr_model=os.environ.get("DOCLING_OCR_MODEL", "qwen/qwen3-vl-32b-instruct"),
        docling_ocr_base_url=os.environ.get(
            "DOCLING_OCR_BASE_URL",
            "https://openrouter.ai/api/v1/chat/completions",
        ),
        docling_ocr_api_key=api_key,
        docling_ocr_page_limit=3,
        docling_ocr_min_chars=500,
        docling_ocr_timeout=180,
    )

    result = DoclingVLMExtractor(cfg).extract(eval_pdf_path.read_bytes(), "application/pdf", {})
    text = result.text.lower()

    assert result.backend == "docling-vlm"
    assert result.quality_signal != "empty"
    assert "attention" in text
    assert "softmax" in text
    assert "sqrt" in text or "√" in result.text or "d_k" in text
    assert "bleu" in text
    assert "transformer" in text
    assert "28.4" in text or "27.3" in text

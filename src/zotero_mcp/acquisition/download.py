from __future__ import annotations
import logging
import tempfile
from pathlib import Path
from .config import AcquisitionConfig
from .http_client import fetch_with_retry, validate_pdf_bytes, validate_content_type
from .types import ArtifactDownload, PipelineError, ProvenanceMetadata

logger = logging.getLogger(__name__)


class ArtifactDownloader:
    def __init__(self, config: AcquisitionConfig):
        self._config = config

    async def download(
        self,
        url: str,
        *,
        expected_type: str = "application/pdf",
        dest_dir: Path | None = None,
    ) -> ArtifactDownload | PipelineError:
        dl_config = self._config.download
        max_bytes = dl_config.max_size_mb * 1024 * 1024

        http_result = await fetch_with_retry(
            url,
            timeout=float(dl_config.timeout_seconds),
            max_retries=3,
        )

        if not http_result.success:
            return PipelineError(
                step="download",
                code="HTTP_ERROR",
                message=f"Download failed: {http_result.error_message}",
                recoverable=True,
            )

        content = http_result.content
        content_type_header = http_result.content_type.split(";")[0].strip()

        if expected_type == "application/pdf":
            if not validate_content_type(content_type_header, "application/pdf"):
                return PipelineError(
                    step="download",
                    code="WRONG_CONTENT_TYPE",
                    message=f"Expected application/pdf but got {content_type_header!r}",
                    recoverable=False,
                )
            if not validate_pdf_bytes(content):
                return PipelineError(
                    step="download",
                    code="INVALID_PDF_BYTES",
                    message="Content does not start with %PDF magic bytes",
                    recoverable=False,
                )

        if len(content) > max_bytes:
            return PipelineError(
                step="download",
                code="SIZE_LIMIT_EXCEEDED",
                message=f"Content size {len(content)} bytes exceeds limit {max_bytes} bytes",
                recoverable=False,
            )

        if dest_dir is None:
            dest_dir = Path(tempfile.mkdtemp())
        dest_dir.mkdir(parents=True, exist_ok=True)

        filename = url.split("/")[-1].split("?")[0] or "download.pdf"
        if not filename.endswith(".pdf") and expected_type == "application/pdf":
            filename += ".pdf"
        file_path = dest_dir / filename
        file_path.write_bytes(content)

        return ArtifactDownload(
            file_path=file_path,
            content_type=content_type_header or expected_type,
            size_bytes=len(content),
            provenance=ProvenanceMetadata(artifact_type="pdf"),
        )

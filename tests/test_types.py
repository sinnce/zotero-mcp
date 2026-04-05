import json
import pytest
from pathlib import Path
from zotero_mcp.acquisition.types import (
    AccessResolution,
    AccessLocation,
    ArtifactDownload,
    ExtractionResult,
    IngestResult,
    ProvenanceMetadata,
    PipelineError,
)


class TestProvenanceMetadata:
    def test_has_exactly_8_fields(self):
        import dataclasses

        fields = [f.name for f in dataclasses.fields(ProvenanceMetadata)]
        expected = {
            "access_source",
            "resolver_name",
            "proxy_provider",
            "artifact_type",
            "extraction_backend",
            "fallback_reason",
            "license",
            "has_fulltext",
        }
        assert set(fields) == expected, f"Got fields: {fields}"

    def test_all_fields_optional(self):
        p = ProvenanceMetadata()
        assert p.access_source is None
        assert p.has_fulltext is None

    def test_to_dict_serializable(self):
        p = ProvenanceMetadata(access_source="unpaywall", resolver_name="Unpaywall OA", has_fulltext=True)
        d = p.to_dict()
        assert d["access_source"] == "unpaywall"
        assert json.dumps(d)


class TestAccessResolution:
    def test_instantiation(self):
        loc = AccessLocation(url="https://example.com/paper.pdf", access_method="oa")
        res = AccessResolution(
            identifier_type="doi", identifier_value="10.1038/nature12373", locations=[loc], best_location=loc
        )
        assert res.best_location.url == "https://example.com/paper.pdf"

    def test_empty_locations(self):
        res = AccessResolution(
            identifier_type="doi", identifier_value="10.xxx/closed", locations=[], best_location=None
        )
        assert res.locations == []
        assert res.best_location is None

    def test_to_dict_serializable(self):
        loc = AccessLocation(url="https://example.com/", access_method="oa", license="cc-by")
        res = AccessResolution(
            identifier_type="doi",
            identifier_value="10.xxx/test",
            locations=[loc],
            best_location=loc,
            metadata={"title": "Test Paper"},
        )
        d = res.to_dict()
        assert json.dumps(d)


class TestExtractionResult:
    def test_quality_signal_empty(self):
        empty = ExtractionResult(text="", backend="pdfminer")
        assert empty.quality_signal == "empty"
        assert empty.char_count == 0

    def test_quality_signal_good(self):
        good = ExtractionResult(text="x" * 600, backend="pdfminer")
        assert good.quality_signal == "good"
        assert good.char_count == 600

    def test_quality_signal_degraded(self):
        degraded = ExtractionResult(text="x" * 100, backend="pdfminer")
        assert degraded.quality_signal == "degraded"

    def test_to_dict(self):
        r = ExtractionResult(text="Some text", backend="pdfminer")
        d = r.to_dict()
        assert d["backend"] == "pdfminer"
        assert json.dumps(d)


class TestPipelineError:
    def test_instantiation(self):
        err = PipelineError(step="download", code="HTTP_404", message="Not found", recoverable=False)
        assert err.recoverable is False

    def test_to_dict(self):
        err = PipelineError(step="resolve", code="NORM_ERROR", message="Bad input", recoverable=True)
        d = err.to_dict()
        assert d["code"] == "NORM_ERROR"
        assert json.dumps(d)

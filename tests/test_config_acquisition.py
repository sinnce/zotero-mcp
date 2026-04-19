"""Tests for acquisition config extension."""

import json
import pytest
import tempfile
from pathlib import Path
from zotero_mcp.acquisition.config import (
    AcquisitionConfig,
    InstitutionalConfig,
    ExtractionConfig,
    DownloadConfig,
    load_acquisition_config,
    ConfigError,
)


@pytest.fixture
def config_without_acquisition(tmp_path):
    """Config file with only semantic_search section."""
    config = {"semantic_search": {"embedding_model": "gemini", "embedding_config": {"model_name": "test"}}}
    p = tmp_path / "config.json"
    p.write_text(json.dumps(config))
    return p


@pytest.fixture
def config_with_full_acquisition(tmp_path):
    """Config file with complete acquisition section."""
    config = {
        "semantic_search": {},
        "acquisition": {
            "unpaywall_email": "user@example.com",
            "s2_enabled": False,
            "s2_api_key": "test-s2-key",
            "pmc_enabled": False,
            "ncbi_email": "ncbi@example.com",
            "auto_ingest": True,
            "institutional_access": {
                "enabled": True,
                "ezproxy_prefix": "proxy.university.edu",
                "openurl_base": "https://link.library.edu/openurl",
            },
            "extraction": {"default_backend": "markitdown", "ocr_fallback": True},
            "download": {"timeout_seconds": 60, "max_size_mb": 200},
        },
    }
    p = tmp_path / "config.json"
    p.write_text(json.dumps(config))
    return p


class TestNoAcquisitionSection:
    def test_loads_without_error(self, config_without_acquisition):
        cfg = load_acquisition_config(config_without_acquisition)
        assert isinstance(cfg, AcquisitionConfig)

    def test_defaults_applied(self, config_without_acquisition):
        cfg = load_acquisition_config(config_without_acquisition)
        assert cfg.unpaywall_email == ""
        assert cfg.institutional_access.enabled is False
        assert cfg.extraction.default_backend == "pdfminer"
        assert cfg.extraction.ocr_fallback is False
        assert cfg.download.timeout_seconds == 30
        assert cfg.download.max_size_mb == 100
        assert cfg.s2_enabled is True
        assert cfg.s2_api_key == ""
        assert cfg.pmc_enabled is True
        assert cfg.ncbi_email == ""
        assert cfg.auto_ingest is False

    def test_semantic_search_unchanged(self, config_without_acquisition):
        """Loading config must not affect semantic_search section."""
        original = json.loads(config_without_acquisition.read_text())
        load_acquisition_config(config_without_acquisition)
        after = json.loads(config_without_acquisition.read_text())
        assert original == after


class TestFullAcquisitionConfig:
    def test_unpaywall_email(self, config_with_full_acquisition):
        cfg = load_acquisition_config(config_with_full_acquisition)
        assert cfg.unpaywall_email == "user@example.com"

    def test_institutional_enabled(self, config_with_full_acquisition):
        cfg = load_acquisition_config(config_with_full_acquisition)
        assert cfg.institutional_access.enabled is True
        assert cfg.institutional_access.ezproxy_prefix == "proxy.university.edu"
        assert cfg.institutional_access.openurl_base == "https://link.library.edu/openurl"

    def test_extraction_config(self, config_with_full_acquisition):
        cfg = load_acquisition_config(config_with_full_acquisition)
        assert cfg.extraction.default_backend == "markitdown"
        assert cfg.extraction.ocr_fallback is True

    def test_download_config(self, config_with_full_acquisition):
        cfg = load_acquisition_config(config_with_full_acquisition)
        assert cfg.download.timeout_seconds == 60
        assert cfg.download.max_size_mb == 200

    def test_resolver_and_ingest_flags(self, config_with_full_acquisition):
        cfg = load_acquisition_config(config_with_full_acquisition)
        assert cfg.s2_enabled is False
        assert cfg.s2_api_key == "test-s2-key"
        assert cfg.pmc_enabled is False
        assert cfg.ncbi_email == "ncbi@example.com"
        assert cfg.auto_ingest is True

    def test_partial_section_uses_defaults(self, tmp_path):
        """Partial acquisition section — missing fields get defaults."""
        config = {"acquisition": {"unpaywall_email": "partial@test.com"}}
        p = tmp_path / "config.json"
        p.write_text(json.dumps(config))
        cfg = load_acquisition_config(p)
        assert cfg.unpaywall_email == "partial@test.com"
        assert cfg.download.timeout_seconds == 30  # Default
        assert cfg.s2_enabled is True
        assert cfg.pmc_enabled is True
        assert cfg.auto_ingest is False

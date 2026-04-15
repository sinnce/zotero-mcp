from __future__ import annotations
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


class ConfigError(ValueError):
    pass


@dataclass
class InstitutionalConfig:
    enabled: bool = False
    ezproxy_prefix: str = ""
    openurl_base: str = ""
    provider: str = "ezproxy"
    libproxy_base_url: str = ""


@dataclass
class ExtractionConfig:
    default_backend: str = "pdfminer"
    ocr_fallback: bool = False
    ocr_model: str = "qwen/qwen3-vl-32b-instruct"
    openrouter_api_key: str = ""
    ocr_page_limit: int = 50


@dataclass
class DownloadConfig:
    timeout_seconds: int = 30
    max_size_mb: int = 100


@dataclass
class AcquisitionConfig:
    unpaywall_email: str = ""
    institutional_access: InstitutionalConfig = field(default_factory=InstitutionalConfig)
    extraction: ExtractionConfig = field(default_factory=ExtractionConfig)
    download: DownloadConfig = field(default_factory=DownloadConfig)
    s2_enabled: bool = True
    s2_api_key: str = ""
    pmc_enabled: bool = True
    ncbi_email: str = ""


def load_acquisition_config(config_path: Path | str | None = None) -> AcquisitionConfig:
    if config_path is None:
        config_path = Path.home() / ".config" / "zotero-mcp" / "config.json"

    config_path = Path(config_path)

    if not config_path.exists():
        logger.debug("Config file not found at %s, using defaults", config_path)
        return AcquisitionConfig()

    try:
        data = json.loads(config_path.read_text())
    except json.JSONDecodeError as e:
        raise ConfigError(f"Invalid JSON in config file {config_path}: {e}")

    acq = data.get("acquisition", {})
    if not acq:
        return AcquisitionConfig()

    inst_raw = acq.get("institutional_access", {})
    institutional = InstitutionalConfig(
        enabled=bool(inst_raw.get("enabled", False)),
        ezproxy_prefix=str(inst_raw.get("ezproxy_prefix", "")),
        openurl_base=str(inst_raw.get("openurl_base", "")),
        provider=str(inst_raw.get("provider", "ezproxy")),
        libproxy_base_url=str(inst_raw.get("libproxy_base_url", "")),
    )

    ext_raw = acq.get("extraction", {})
    extraction = ExtractionConfig(
        default_backend=str(ext_raw.get("default_backend", "pdfminer")),
        ocr_fallback=bool(ext_raw.get("ocr_fallback", False)),
        ocr_model=str(ext_raw.get("ocr_model", "qwen/qwen3-vl-32b-instruct")),
        openrouter_api_key=str(ext_raw.get("openrouter_api_key", "")),
        ocr_page_limit=int(ext_raw.get("ocr_page_limit", 50)),
    )

    dl_raw = acq.get("download", {})
    download = DownloadConfig(
        timeout_seconds=int(dl_raw.get("timeout_seconds", 30)),
        max_size_mb=int(dl_raw.get("max_size_mb", 100)),
    )

    return AcquisitionConfig(
        unpaywall_email=str(acq.get("unpaywall_email", "")),
        institutional_access=institutional,
        extraction=extraction,
        download=download,
    )

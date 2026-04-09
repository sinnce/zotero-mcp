from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ProvenanceMetadata:
    access_source: str | None = None
    resolver_name: str | None = None
    proxy_provider: str | None = None
    artifact_type: str | None = None
    extraction_backend: str | None = None
    fallback_reason: str | None = None
    license: str | None = None
    has_fulltext: bool | None = None
    quality_signal: str | None = None
    bridge_session: str | None = None

    def to_dict(self) -> dict:
        return {
            "access_source": self.access_source,
            "resolver_name": self.resolver_name,
            "proxy_provider": self.proxy_provider,
            "artifact_type": self.artifact_type,
            "extraction_backend": self.extraction_backend,
            "fallback_reason": self.fallback_reason,
            "license": self.license,
            "has_fulltext": self.has_fulltext,
            "quality_signal": self.quality_signal,
            "bridge_session": self.bridge_session,
        }


@dataclass
class AccessLocation:
    url: str
    access_method: str
    license: str | None = None
    version: str | None = None
    requires_session: bool = False
    session_kind: str | None = None

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "access_method": self.access_method,
            "license": self.license,
            "version": self.version,
            "requires_session": self.requires_session,
            "session_kind": self.session_kind,
        }


@dataclass
class AccessResolution:
    identifier_type: str
    identifier_value: str
    locations: list[AccessLocation] = field(default_factory=list)
    best_location: AccessLocation | None = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "identifier_type": self.identifier_type,
            "identifier_value": self.identifier_value,
            "locations": [loc.to_dict() for loc in self.locations],
            "best_location": self.best_location.to_dict() if self.best_location else None,
            "metadata": self.metadata,
        }


@dataclass
class ArtifactDownload:
    file_path: Path
    content_type: str
    size_bytes: int
    provenance: ProvenanceMetadata = field(default_factory=ProvenanceMetadata)

    def to_dict(self) -> dict:
        return {
            "file_path": str(self.file_path),
            "content_type": self.content_type,
            "size_bytes": self.size_bytes,
            "provenance": self.provenance.to_dict(),
        }


@dataclass
class ExtractionResult:
    text: str
    backend: str
    char_count: int = 0
    quality_signal: str = "empty"
    fallback_chain: list[str] = field(default_factory=list)

    def __post_init__(self):
        self.char_count = len(self.text)
        if self.char_count >= 500:
            self.quality_signal = "good"
        elif self.char_count > 0:
            self.quality_signal = "degraded"
        else:
            self.quality_signal = "empty"

    def to_dict(self) -> dict:
        return {
            "text_preview": self.text[:500],
            "backend": self.backend,
            "char_count": self.char_count,
            "quality_signal": self.quality_signal,
            "fallback_chain": self.fallback_chain,
        }


@dataclass
class IngestResult:
    item_key: str
    attachment_key: str | None = None
    provenance: ProvenanceMetadata = field(default_factory=ProvenanceMetadata)

    def to_dict(self) -> dict:
        return {
            "item_key": self.item_key,
            "attachment_key": self.attachment_key,
            "provenance": self.provenance.to_dict(),
        }


@dataclass
class PipelineError:
    step: str
    code: str
    message: str
    recoverable: bool = False

    def to_dict(self) -> dict:
        return {
            "step": self.step,
            "code": self.code,
            "message": self.message,
            "recoverable": self.recoverable,
        }

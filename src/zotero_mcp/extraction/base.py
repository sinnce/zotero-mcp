from __future__ import annotations
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


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


class NoExtractorError(Exception):
    pass


class Extractor(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    def supports(self, content_type: str) -> bool:
        pass

    @abstractmethod
    def extract(self, content: bytes, content_type: str, metadata: dict) -> ExtractionResult:
        pass


class ExtractorRegistry:
    def __init__(self):
        self._extractors: list[Extractor] = []

    def register(self, extractor: Extractor) -> None:
        self._extractors.append(extractor)

    def get_extractor(self, content_type: str) -> Extractor:
        for ext in self._extractors:
            if ext.supports(content_type):
                return ext
        raise NoExtractorError(
            f"No extractor registered for content type: {content_type!r}. "
            f"Registered: {[e.name for e in self._extractors]}"
        )

    def extract_with_fallback(
        self,
        content: bytes,
        content_type: str,
        metadata: dict | None = None,
    ) -> ExtractionResult:
        if metadata is None:
            metadata = {}

        candidates = [e for e in self._extractors if e.supports(content_type)]
        if not candidates:
            logger.debug("No extractor supports %r", content_type)
            return ExtractionResult(
                text="",
                backend="none",
                fallback_chain=[f"No extractor for {content_type!r}"],
            )

        fallback_chain: list[str] = []
        for extractor in candidates:
            try:
                result = extractor.extract(content, content_type, metadata)
                if fallback_chain:
                    result.fallback_chain = fallback_chain + [f"{extractor.name}: success"]
                return result
            except Exception as e:
                reason = f"{extractor.name}: {type(e).__name__}({e})"
                fallback_chain.append(reason)
                logger.debug("Extractor %r failed: %s", extractor.name, e)

        return ExtractionResult(
            text="",
            backend="none",
            fallback_chain=fallback_chain,
        )

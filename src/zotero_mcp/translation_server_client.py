from __future__ import annotations

import os
from dataclasses import dataclass

import requests

DEFAULT_TRANSLATION_SERVER_URL = os.environ.get("ZOTERO_TRANSLATION_SERVER_URL", "http://127.0.0.1:1969")


@dataclass
class TranslationServerStatus:
    available: bool
    base_url: str
    status_code: int | None = None
    message: str | None = None

    def to_dict(self) -> dict:
        return {
            "available": self.available,
            "base_url": self.base_url,
            "status_code": self.status_code,
            "message": self.message,
        }


class TranslationServerError(RuntimeError):
    pass


class TranslationServerClient:
    def __init__(self, base_url: str = DEFAULT_TRANSLATION_SERVER_URL, timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def status(self) -> TranslationServerStatus:
        probe_urls = (f"{self.base_url}/web", f"{self.base_url}/search", self.base_url)
        for probe_url in probe_urls:
            try:
                resp = requests.get(probe_url, timeout=2)
                if resp.status_code < 500:
                    return TranslationServerStatus(
                        available=True,
                        base_url=self.base_url,
                        status_code=resp.status_code,
                        message=f"Translation server responded on {probe_url}",
                    )
            except requests.RequestException:
                continue

        return TranslationServerStatus(
            available=False,
            base_url=self.base_url,
            message="Could not connect to translation-server",
        )

    def _post_text(self, endpoint: str, value: str):
        if not value.strip():
            raise TranslationServerError("Input value must not be empty")

        try:
            resp = requests.post(
                f"{self.base_url}/{endpoint.lstrip('/')}",
                data=value.encode("utf-8"),
                headers={"Content-Type": "text/plain"},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise TranslationServerError(f"translation-server request failed: {exc}") from exc

        if resp.status_code >= 400:
            raise TranslationServerError(f"translation-server returned HTTP {resp.status_code}: {resp.text[:300]}")

        try:
            return resp.json()
        except ValueError as exc:
            raise TranslationServerError("translation-server returned non-JSON response") from exc

    def translate_web(self, url: str):
        return self._post_text("web", url)

    def translate_search(self, query: str):
        return self._post_text("search", query)

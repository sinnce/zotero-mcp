from __future__ import annotations
import logging
from urllib.parse import urlparse, urlunparse, urlencode
from .config import AcquisitionConfig
from .types import AccessResolution, AccessLocation

logger = logging.getLogger(__name__)


class ConfigError(ValueError):
    pass


def build_ezproxy_url(url: str, proxy_prefix: str) -> str:
    parsed = urlparse(url)
    hostname_rewritten = parsed.netloc.replace(".", "-")
    new_netloc = f"{hostname_rewritten}.{proxy_prefix}"
    return urlunparse((parsed.scheme, new_netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))


def build_libproxy_url(url: str, base_url: str) -> str:
    params = urlencode({"url": url})
    return f"{base_url}?{params}"


def build_openurl(doi: str, metadata: dict) -> str:
    params: dict[str, str] = {"rft_id": f"doi:{doi}", "rft.genre": "article"}
    if metadata.get("title"):
        params["rft.title"] = metadata["title"]
    if metadata.get("author"):
        params["rft.au"] = metadata["author"]
    if metadata.get("journal"):
        params["rft.jtitle"] = metadata["journal"]
    return urlencode(params)


class InstitutionalResolver:
    def __init__(self, config: AcquisitionConfig):
        self._config = config

    async def resolve(self, doi: str, metadata: dict) -> AccessResolution:
        inst = self._config.institutional_access
        if not inst.enabled:
            return AccessResolution(identifier_type="doi", identifier_value=doi)

        doi_url = f"https://doi.org/{doi}"

        if inst.provider == "libproxy":
            if not inst.libproxy_base_url:
                raise ConfigError("institutional_access.libproxy_base_url must be set when provider='libproxy'")
            proxied = build_libproxy_url(doi_url, inst.libproxy_base_url)
            locations = [
                AccessLocation(
                    url=proxied,
                    access_method="institutional",
                    requires_session=True,
                    session_kind="libproxy",
                )
            ]
        else:
            if not inst.ezproxy_prefix:
                raise ConfigError("institutional_access.ezproxy_prefix must be set when enabled=True")
            proxied = build_ezproxy_url(doi_url, inst.ezproxy_prefix)
            locations = [AccessLocation(url=proxied, access_method="institutional")]

        return AccessResolution(
            identifier_type="doi",
            identifier_value=doi,
            locations=locations,
            best_location=locations[0],
        )

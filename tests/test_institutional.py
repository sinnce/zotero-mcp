import pytest
from zotero_mcp.acquisition.institutional import (
    InstitutionalResolver,
    build_openurl,
    build_ezproxy_url,
    build_libproxy_url,
    ConfigError,
)
from zotero_mcp.acquisition.config import AcquisitionConfig, InstitutionalConfig
from zotero_mcp.acquisition.types import AccessResolution, AccessLocation


class TestEZproxyURL:
    def test_basic_doi_url(self):
        result = build_ezproxy_url("https://doi.org/10.1038/nature12373", "proxy.university.edu")
        assert result == "https://doi-org.proxy.university.edu/10.1038/nature12373"

    def test_subdomain_dots_replaced(self):
        result = build_ezproxy_url("https://www.nature.com/articles/foo", "proxy.library.ac.uk")
        assert "www-nature-com" in result
        assert "proxy.library.ac.uk" in result

    def test_path_preserved(self):
        result = build_ezproxy_url("https://example.com/article/123", "proxy.uni.edu")
        assert "/article/123" in result

    def test_https_maintained(self):
        result = build_ezproxy_url("https://example.com/paper", "proxy.uni.edu")
        assert result.startswith("https://")

    def test_query_string_preserved(self):
        result = build_ezproxy_url("https://example.com/search?q=test&page=1", "proxy.uni.edu")
        assert "q=test" in result
        assert "page=1" in result

    def test_nested_subdomain(self):
        result = build_ezproxy_url("https://link.springer.com/article/10.1007/s00222-015-0629-4", "proxy.lib.edu")
        assert "link-springer-com" in result
        assert "proxy.lib.edu" in result


class TestOpenURL:
    def test_doi_openurl(self):
        result = build_openurl("10.1038/nature12373", {})
        assert "doi" in result.lower() or "10.1038" in result

    def test_with_metadata(self):
        result = build_openurl("10.xxx/test", {"title": "Test Paper", "author": "Smith"})
        assert "Test+Paper" in result or "Test%20Paper" in result or "title" in result

    def test_doi_in_rft_id(self):
        result = build_openurl("10.1234/example", {})
        assert "rft_id" in result
        assert "10.1234" in result

    def test_journal_metadata(self):
        result = build_openurl("10.xxx/test", {"journal": "Nature"})
        assert "Nature" in result

    def test_empty_metadata(self):
        result = build_openurl("10.xxx/test", {})
        assert isinstance(result, str)
        assert len(result) > 0


class TestInstitutionalResolver:
    def test_disabled_returns_empty(self):
        config = AcquisitionConfig()
        resolver = InstitutionalResolver(config)
        import asyncio

        result = asyncio.run(resolver.resolve("10.xxx/test", {}))
        assert isinstance(result, AccessResolution)
        assert result.locations == []

    def test_enabled_returns_locations(self):
        config = AcquisitionConfig(
            institutional_access=InstitutionalConfig(enabled=True, ezproxy_prefix="proxy.university.edu")
        )
        resolver = InstitutionalResolver(config)
        import asyncio

        result = asyncio.run(resolver.resolve("10.1038/nature12373", {}))
        assert len(result.locations) >= 1
        assert result.locations[0].access_method == "institutional"

    def test_enabled_no_prefix_raises(self):
        config = AcquisitionConfig(institutional_access=InstitutionalConfig(enabled=True, ezproxy_prefix=""))
        resolver = InstitutionalResolver(config)
        import asyncio

        with pytest.raises(ConfigError):
            asyncio.run(resolver.resolve("10.xxx/test", {}))

    def test_enabled_sets_best_location(self):
        config = AcquisitionConfig(
            institutional_access=InstitutionalConfig(enabled=True, ezproxy_prefix="proxy.university.edu")
        )
        resolver = InstitutionalResolver(config)
        import asyncio

        result = asyncio.run(resolver.resolve("10.1038/nature12373", {}))
        assert result.best_location is not None
        assert result.best_location.url == result.locations[0].url

    def test_proxied_url_contains_doi(self):
        config = AcquisitionConfig(
            institutional_access=InstitutionalConfig(enabled=True, ezproxy_prefix="proxy.university.edu")
        )
        resolver = InstitutionalResolver(config)
        import asyncio

        result = asyncio.run(resolver.resolve("10.1038/nature12373", {}))
        assert "10.1038/nature12373" in result.locations[0].url

    def test_resolution_identifier_preserved(self):
        config = AcquisitionConfig(
            institutional_access=InstitutionalConfig(enabled=True, ezproxy_prefix="proxy.university.edu")
        )
        resolver = InstitutionalResolver(config)
        import asyncio

        result = asyncio.run(resolver.resolve("10.1038/nature12373", {}))
        assert result.identifier_type == "doi"
        assert result.identifier_value == "10.1038/nature12373"


class TestLibproxyURL:
    def test_basic_url(self):
        result = build_libproxy_url("https://doi.org/10.1038/nature12373", "https://libproxy.snu.ac.kr/link.n2s")
        assert "libproxy.snu.ac.kr/link.n2s" in result
        assert "url=" in result
        assert "doi.org" in result

    def test_url_encoded(self):
        result = build_libproxy_url("https://example.com/path?q=1", "https://proxy.edu/link.n2s")
        assert "%3A" in result or "%2F" in result

    def test_base_url_preserved(self):
        result = build_libproxy_url("https://doi.org/10.1234/test", "https://libproxy.example.edu/link.n2s")
        assert result.startswith("https://libproxy.example.edu/link.n2s?")


class TestLibproxyResolver:
    def test_libproxy_returns_session_required(self):
        config = AcquisitionConfig(
            institutional_access=InstitutionalConfig(
                enabled=True,
                provider="libproxy",
                libproxy_base_url="https://libproxy.snu.ac.kr/link.n2s",
            )
        )
        resolver = InstitutionalResolver(config)
        import asyncio

        result = asyncio.run(resolver.resolve("10.1038/nature12373", {}))
        assert len(result.locations) >= 1
        assert result.locations[0].requires_session is True
        assert result.locations[0].session_kind == "libproxy"

    def test_libproxy_no_base_url_raises(self):
        config = AcquisitionConfig(
            institutional_access=InstitutionalConfig(
                enabled=True,
                provider="libproxy",
                libproxy_base_url="",
            )
        )
        resolver = InstitutionalResolver(config)
        import asyncio

        with pytest.raises(ConfigError):
            asyncio.run(resolver.resolve("10.xxx/test", {}))

    def test_libproxy_url_contains_doi(self):
        config = AcquisitionConfig(
            institutional_access=InstitutionalConfig(
                enabled=True,
                provider="libproxy",
                libproxy_base_url="https://libproxy.snu.ac.kr/link.n2s",
            )
        )
        resolver = InstitutionalResolver(config)
        import asyncio

        result = asyncio.run(resolver.resolve("10.1038/nature12373", {}))
        assert "10.1038" in result.locations[0].url

    def test_default_provider_is_ezproxy(self):
        config = AcquisitionConfig(
            institutional_access=InstitutionalConfig(
                enabled=True,
                ezproxy_prefix="proxy.university.edu",
            )
        )
        assert config.institutional_access.provider == "ezproxy"

    def test_access_location_session_fields(self):
        loc = AccessLocation(
            url="https://example.com", access_method="institutional", requires_session=True, session_kind="libproxy"
        )
        d = loc.to_dict()
        assert d["requires_session"] is True
        assert d["session_kind"] == "libproxy"

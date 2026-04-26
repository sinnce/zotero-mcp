from unittest.mock import AsyncMock, patch

import pytest
from pytest_httpx import HTTPXMock

from zotero_mcp.acquisition.identifier import CanonicalId
from zotero_mcp.acquisition.resolver import resolve_access
from zotero_mcp.acquisition.types import AccessLocation, AccessResolution
from zotero_mcp.acquisition.url_translators import (
    IEEEXploreTranslator,
    _extract_doi_from_ieee_html,
    build_default_url_translator_registry,
)

IEEE_HTML_META = """
<html><head>
<meta name="citation_doi" content="10.1109/TPAMI.2017.2699184" />
</head><body>IEEE</body></html>
"""

IEEE_HTML_JS = """
<html><head></head><body>
<script>
xplGlobal.document.metadata = {"doi":"10.1109/TPAMI.2017.2699184","articleNumber":"7974879"};
</script>
</body></html>
"""

IEEE_HTML_JS_NESTED = """
<html><head></head><body>
<script>
xplGlobal.document.metadata = {"authors":[{"name":"Author One"}],"doi":"10.1109/TPAMI.2017.2699184","articleNumber":"7974879"};
</script>
</body></html>
"""


def test_extract_doi_from_ieee_html_meta():
    assert _extract_doi_from_ieee_html(IEEE_HTML_META) == "10.1109/TPAMI.2017.2699184"


def test_extract_doi_from_ieee_html_js_blob():
    assert _extract_doi_from_ieee_html(IEEE_HTML_JS) == "10.1109/TPAMI.2017.2699184"


def test_extract_doi_from_ieee_html_nested_js_blob():
    assert _extract_doi_from_ieee_html(IEEE_HTML_JS_NESTED) == "10.1109/TPAMI.2017.2699184"


@pytest.mark.asyncio
async def test_ieee_url_upgrades_to_doi_and_uses_unpaywall(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="https://ieeexplore.ieee.org/document/7974879",
        text=IEEE_HTML_META,
        headers={"content-type": "text/html"},
    )

    loc = AccessLocation(url="https://example.com/paper.pdf", access_method="oa", license="cc-by")
    unpaywall_result = AccessResolution(
        identifier_type="doi",
        identifier_value="10.1109/TPAMI.2017.2699184",
        locations=[loc],
        best_location=loc,
        metadata={"title": "Geometric Deep Learning"},
    )

    s2_empty = AccessResolution(
        identifier_type="doi",
        identifier_value="10.1109/TPAMI.2017.2699184",
    )
    pmc_empty = AccessResolution(
        identifier_type="doi",
        identifier_value="10.1109/TPAMI.2017.2699184",
    )
    with (
        patch("zotero_mcp.acquisition.resolver._translation_server_item_for_url", return_value=None),
        patch(
            "zotero_mcp.acquisition.unpaywall.UnpaywallClient.resolve",
            new=AsyncMock(return_value=unpaywall_result),
        ) as mocked_resolve,
        patch(
            "zotero_mcp.acquisition.semantic_scholar.SemanticScholarResolver.resolve",
            new=AsyncMock(return_value=s2_empty),
        ),
        patch(
            "zotero_mcp.acquisition.pmc_oa.PMCOAResolver.resolve",
            new=AsyncMock(return_value=pmc_empty),
        ),
    ):
        result = await resolve_access("https://ieeexplore.ieee.org/document/7974879")

    mocked_resolve.assert_awaited_once_with("10.1109/TPAMI.2017.2699184")
    assert result.identifier_type == "doi"
    assert result.identifier_value == "10.1109/TPAMI.2017.2699184"
    assert result.best_location is not None
    assert result.best_location.url == "https://example.com/paper.pdf"


@pytest.mark.asyncio
async def test_ieee_url_without_doi_falls_back_to_direct(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="https://ieeexplore.ieee.org/document/7974879",
        text="<html><body>No DOI here</body></html>",
        headers={"content-type": "text/html"},
    )

    with patch("zotero_mcp.acquisition.resolver._translation_server_item_for_url", return_value=None):
        result = await resolve_access("https://ieeexplore.ieee.org/document/7974879")

    assert result.identifier_type == "url"
    assert result.best_location is not None
    assert result.best_location.url == "https://ieeexplore.ieee.org/document/7974879"
    assert result.best_location.access_method == "direct"


@pytest.mark.asyncio
async def test_non_ieee_url_stays_direct_without_fetching():
    with patch("zotero_mcp.acquisition.resolver._translation_server_item_for_url", return_value=None):
        result = await resolve_access("https://example.com/paper")

    assert result.identifier_type == "url"
    assert result.best_location is not None
    assert result.best_location.url == "https://example.com/paper"
    assert result.best_location.access_method == "direct"


def test_default_registry_registers_ieee_translator():
    registry = build_default_url_translator_registry()
    assert any(isinstance(translator, IEEEXploreTranslator) for translator in registry._translators)


def test_ieee_translator_supports_ieee_urls_only():
    translator = IEEEXploreTranslator()
    assert translator.supports(
        CanonicalId(type="url", value="https://ieeexplore.ieee.org/document/7974879", raw_input="x")
    )
    assert not translator.supports(CanonicalId(type="url", value="https://example.com/paper", raw_input="x"))


@pytest.mark.asyncio
async def test_translation_server_url_upgrades_generic_url_to_doi():
    translated_item = {
        "title": "Translated Paper",
        "DOI": "10.1109/TPAMI.2017.2699184",
        "creators": [{"firstName": "Jane", "lastName": "Doe"}],
        "proceedingsTitle": "Proceedings",
        "date": "2017-01",
    }
    loc = AccessLocation(url="https://example.com/paper.pdf", access_method="oa", license="cc-by")
    unpaywall_result = AccessResolution(
        identifier_type="doi",
        identifier_value="10.1109/TPAMI.2017.2699184",
        locations=[loc],
        best_location=loc,
        metadata={},
    )
    empty = AccessResolution(identifier_type="doi", identifier_value="10.1109/TPAMI.2017.2699184")

    with (
        patch(
            "zotero_mcp.acquisition.resolver._translation_server_item_for_url",
            return_value=translated_item,
        ),
        patch(
            "zotero_mcp.acquisition.unpaywall.UnpaywallClient.resolve",
            new=AsyncMock(return_value=unpaywall_result),
        ) as mocked_resolve,
        patch(
            "zotero_mcp.acquisition.semantic_scholar.SemanticScholarResolver.resolve",
            new=AsyncMock(return_value=empty),
        ),
        patch(
            "zotero_mcp.acquisition.pmc_oa.PMCOAResolver.resolve",
            new=AsyncMock(return_value=empty),
        ),
    ):
        result = await resolve_access("https://example.com/paper")

    mocked_resolve.assert_awaited_once_with("10.1109/TPAMI.2017.2699184")
    assert result.identifier_type == "doi"
    assert result.metadata["title"] == "Translated Paper"
    assert result.metadata["authors"] == "Jane Doe"
    assert result.metadata["journal"] == "Proceedings"
    assert result.metadata["year"] == "2017"


@pytest.mark.asyncio
async def test_translation_server_adds_pdf_attachment_location_for_url():
    translated_item = {
        "title": "Translated PDF",
        "attachments": [
            {"url": "https://example.com/full.pdf", "mimeType": "application/pdf", "title": "Full Text PDF"}
        ],
    }

    with patch(
        "zotero_mcp.acquisition.resolver._translation_server_item_for_url",
        return_value=translated_item,
    ):
        result = await resolve_access("https://example.com/paper")

    assert result.identifier_type == "url"
    assert len(result.locations) == 2
    assert result.locations[0].access_method == "direct"
    assert result.locations[1].access_method == "translation-server"
    assert result.locations[1].url == "https://example.com/full.pdf"


@pytest.mark.asyncio
async def test_translation_server_preserves_source_url_when_doi_has_no_oa_locations():
    translated_item = {
        "title": "Translated Paper",
        "DOI": "10.1109/TPAMI.2017.2699184",
    }
    empty = AccessResolution(identifier_type="doi", identifier_value="10.1109/TPAMI.2017.2699184")

    with (
        patch(
            "zotero_mcp.acquisition.resolver._translation_server_item_for_url",
            return_value=translated_item,
        ),
        patch(
            "zotero_mcp.acquisition.unpaywall.UnpaywallClient.resolve",
            new=AsyncMock(return_value=empty),
        ),
        patch(
            "zotero_mcp.acquisition.semantic_scholar.SemanticScholarResolver.resolve",
            new=AsyncMock(return_value=empty),
        ),
        patch(
            "zotero_mcp.acquisition.pmc_oa.PMCOAResolver.resolve",
            new=AsyncMock(return_value=empty),
        ),
    ):
        result = await resolve_access("https://example.com/paper")

    assert result.identifier_type == "doi"
    assert result.best_location is not None
    assert result.best_location.access_method == "direct"
    assert result.best_location.url == "https://example.com/paper"


@pytest.mark.asyncio
async def test_translation_server_pdf_attachment_preferred_when_doi_has_no_oa_locations():
    translated_item = {
        "title": "Translated Paper",
        "DOI": "10.1109/TPAMI.2017.2699184",
        "attachments": [
            {"url": "https://example.com/full.pdf", "mimeType": "application/pdf", "title": "Full Text PDF"}
        ],
    }
    empty = AccessResolution(identifier_type="doi", identifier_value="10.1109/TPAMI.2017.2699184")

    with (
        patch(
            "zotero_mcp.acquisition.resolver._translation_server_item_for_url",
            return_value=translated_item,
        ),
        patch(
            "zotero_mcp.acquisition.unpaywall.UnpaywallClient.resolve",
            new=AsyncMock(return_value=empty),
        ),
        patch(
            "zotero_mcp.acquisition.semantic_scholar.SemanticScholarResolver.resolve",
            new=AsyncMock(return_value=empty),
        ),
        patch(
            "zotero_mcp.acquisition.pmc_oa.PMCOAResolver.resolve",
            new=AsyncMock(return_value=empty),
        ),
    ):
        result = await resolve_access("https://example.com/paper")

    assert result.identifier_type == "doi"
    assert result.best_location is not None
    assert result.best_location.access_method == "translation-server"
    assert result.best_location.url == "https://example.com/full.pdf"

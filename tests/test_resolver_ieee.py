import pytest
from pytest_httpx import HTTPXMock
from unittest.mock import AsyncMock, patch

from zotero_mcp.acquisition.resolver import _extract_doi_from_ieee_html, resolve_access
from zotero_mcp.acquisition.types import AccessLocation, AccessResolution


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
    with (
        patch(
            "zotero_mcp.acquisition.unpaywall.UnpaywallClient.resolve",
            new=AsyncMock(return_value=unpaywall_result),
        ) as mocked_resolve,
        patch(
            "zotero_mcp.acquisition.semantic_scholar.SemanticScholarResolver.resolve",
            new=AsyncMock(return_value=s2_empty),
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

    result = await resolve_access("https://ieeexplore.ieee.org/document/7974879")

    assert result.identifier_type == "url"
    assert result.best_location is not None
    assert result.best_location.url == "https://ieeexplore.ieee.org/document/7974879"
    assert result.best_location.access_method == "direct"


@pytest.mark.asyncio
async def test_non_ieee_url_stays_direct_without_fetching():
    result = await resolve_access("https://example.com/paper")

    assert result.identifier_type == "url"
    assert result.best_location is not None
    assert result.best_location.url == "https://example.com/paper"
    assert result.best_location.access_method == "direct"

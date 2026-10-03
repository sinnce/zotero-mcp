from unittest.mock import MagicMock, patch

import pytest

from zotero_mcp.acquisition.ingest import ingest_paper
from zotero_mcp.acquisition.types import IngestResult
from zotero_mcp.tools.ingest_paper_to_zotero import ingest_paper_to_zotero


@pytest.fixture
def mock_ctx():
    ctx = MagicMock()
    ctx.info = MagicMock()
    return ctx


def make_mock_zotero(item_key="TEST_KEY_123", has_duplicate=False):
    zot = MagicMock()
    zot.item_template.return_value = {
        "itemType": "journalArticle",
        "title": "",
        "DOI": "",
        "date": "",
        "publicationTitle": "",
        "abstractNote": "",
        "creators": [],
        "extra": "",
    }
    if has_duplicate:
        zot.items.return_value = [{"data": {"DOI": "10.xxx/existing", "key": "EXISTING_KEY"}}]
    else:
        zot.items.return_value = []

    zot.create_items.return_value = {
        "successful": {"0": {"key": item_key}},
        "failed": {},
    }
    zot.children.return_value = []
    return zot


class TestIngestPaperToZotero:
    @pytest.mark.parametrize(
        ("ingest_kwargs", "existing_data"),
        [
            (
                {"doi": "10.1234/existing"},
                {"DOI": "10.1234/existing", "key": "EXISTING_DOI_KEY"},
            ),
            (
                {"arxiv_id": "2401.12345"},
                {"extra": "arXiv:2401.12345", "key": "EXISTING_ARXIV_KEY"},
            ),
        ],
        ids=["doi", "arxiv"],
    )
    def test_uses_write_client_when_read_client_is_unavailable_for_duplicates(self, ingest_kwargs, existing_data):
        read_zot = make_mock_zotero()
        write_zot = make_mock_zotero()
        read_zot.items.side_effect = ConnectionRefusedError("connection refused")
        write_zot.items.return_value = [{"data": existing_data}]

        result = ingest_paper(
            write_zot=write_zot,
            read_zot=read_zot,
            title="Existing Paper",
            **ingest_kwargs,
        )

        assert isinstance(result, IngestResult)
        assert result.item_key == existing_data["key"]
        write_zot.create_items.assert_not_called()
        write_zot.attachment_both.assert_not_called()

    def test_does_not_repeat_duplicate_query_for_shared_client(self):
        zot = make_mock_zotero()

        result = ingest_paper(
            write_zot=zot,
            read_zot=zot,
            title="New Paper",
            doi="10.1234/new",
        )

        assert isinstance(result, IngestResult)
        assert zot.items.call_count == 1
        zot.create_items.assert_called_once()

    def test_creates_item_when_both_duplicate_clients_are_unavailable(self):
        read_zot = make_mock_zotero()
        write_zot = make_mock_zotero()
        read_zot.items.side_effect = ConnectionRefusedError("connection refused")
        write_zot.items.side_effect = ConnectionRefusedError("connection refused")

        result = ingest_paper(
            write_zot=write_zot,
            read_zot=read_zot,
            title="New Paper",
            doi="10.1234/new",
        )

        assert isinstance(result, IngestResult)
        assert read_zot.items.call_count == 1
        assert write_zot.items.call_count == 1
        write_zot.create_items.assert_called_once()

    @pytest.mark.parametrize(
        ("authors", "expected_creators"),
        [
            (
                [" Ada Lovelace ", "", "Grace Hopper"],
                [
                    {"creatorType": "author", "name": "Ada Lovelace"},
                    {"creatorType": "author", "name": "Grace Hopper"},
                ],
            ),
            (
                " Ada Lovelace, , Grace Hopper ",
                [
                    {"creatorType": "author", "name": "Ada Lovelace"},
                    {"creatorType": "author", "name": "Grace Hopper"},
                ],
            ),
        ],
        ids=["resolver_author_list", "manual_author_string"],
    )
    def test_normalizes_author_names_at_ingest_boundary(self, authors, expected_creators):
        # Given: canonical resolver or manual-tool author metadata.
        mock_zot = make_mock_zotero()

        # When: the metadata is ingested.
        result = ingest_paper(
            write_zot=mock_zot,
            read_zot=mock_zot,
            title="Test Paper",
            authors=authors,
        )

        # Then: Zotero receives ordered non-empty creator names.
        assert isinstance(result, IngestResult)
        assert mock_zot.create_items.call_args[0][0][0]["creators"] == expected_creators

    def test_no_write_client_returns_error(self, mock_ctx):
        with patch(
            "zotero_mcp.tools.ingest_paper_to_zotero._get_write_client", side_effect=ValueError("No web client")
        ):
            result = ingest_paper_to_zotero("Test Paper", ctx=mock_ctx)
        assert isinstance(result, str)
        assert "Error" in result or "ZOTERO_API_KEY" in result

    def test_successful_ingest(self, mock_ctx):
        mock_zot = make_mock_zotero("NEW_ITEM_KEY")
        with patch("zotero_mcp.tools.ingest_paper_to_zotero._get_write_client", return_value=(mock_zot, mock_zot)):
            result = ingest_paper_to_zotero("Test Paper", doi="10.xxx/test", ctx=mock_ctx)
        assert isinstance(result, str)
        assert "NEW_ITEM_KEY" in result or "Ingested" in result

    def test_duplicate_detection(self, mock_ctx):
        mock_zot = make_mock_zotero(has_duplicate=True)
        with patch("zotero_mcp.tools.ingest_paper_to_zotero._get_write_client", return_value=(mock_zot, mock_zot)):
            result = ingest_paper_to_zotero("Existing Paper", doi="10.xxx/existing", ctx=mock_ctx)
        assert isinstance(result, str)
        assert "Duplicate" in result or "EXISTING_KEY" in result

    def test_returns_string_always(self, mock_ctx):
        mock_zot = make_mock_zotero()
        with patch("zotero_mcp.tools.ingest_paper_to_zotero._get_write_client", return_value=(mock_zot, mock_zot)):
            result = ingest_paper_to_zotero("Test", ctx=mock_ctx)
        assert isinstance(result, str)

    def test_create_failed_returns_error_string(self, mock_ctx):
        mock_zot = make_mock_zotero()
        mock_zot.create_items.return_value = {"successful": {}, "failed": {"0": "error"}}
        with patch("zotero_mcp.tools.ingest_paper_to_zotero._get_write_client", return_value=(mock_zot, mock_zot)):
            result = ingest_paper_to_zotero("Test", ctx=mock_ctx)
        assert isinstance(result, str)
        assert "failed" in result.lower() or "error" in result.lower()

    def test_exception_in_write_returns_error_not_raises(self, mock_ctx):
        with patch(
            "zotero_mcp.tools.ingest_paper_to_zotero._get_write_client", side_effect=Exception("connection refused")
        ):
            result = ingest_paper_to_zotero("Test", ctx=mock_ctx)
        assert isinstance(result, str)
        assert "Error" in result

    def test_successful_ingest_with_full_metadata(self, mock_ctx):
        mock_zot = make_mock_zotero("FULL_META_KEY")
        with patch("zotero_mcp.tools.ingest_paper_to_zotero._get_write_client", return_value=(mock_zot, mock_zot)):
            result = ingest_paper_to_zotero(
                "Complete Paper",
                doi="10.xxx/full",
                authors="Smith J, Jones K",
                journal="Nature",
                year="2024",
                abstract="An abstract.",
                ctx=mock_ctx,
            )
        assert isinstance(result, str)
        assert "FULL_META_KEY" in result or "Ingested" in result
        mock_zot.create_items.assert_called_once()
        template_arg = mock_zot.create_items.call_args[0][0][0]
        assert template_arg["title"] == "Complete Paper"
        assert template_arg["DOI"] == "10.xxx/full"
        assert len(template_arg["creators"]) == 2

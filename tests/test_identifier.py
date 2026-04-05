import pytest
from zotero_mcp.acquisition.identifier import CanonicalId, normalize, NormalizationError


class TestDOINormalization:
    def test_doi_plain(self):
        r = normalize("10.1038/nature12373")
        assert r.type == "doi" and r.value == "10.1038/nature12373"

    def test_doi_url_prefix(self):
        r = normalize("https://doi.org/10.1038/nature12373")
        assert r.type == "doi" and r.value == "10.1038/nature12373"

    def test_doi_doi_prefix(self):
        r = normalize("doi:10.1038/nature12373")
        assert r.type == "doi" and r.value == "10.1038/nature12373"

    def test_doi_special_chars(self):
        r = normalize("10.1002/(SICI)1096-987X(199604)17:5/6<643::AID-JCC6>3.0.CO;2-M")
        assert r.type == "doi"


class TestArxivNormalization:
    def test_arxiv_new_format(self):
        r = normalize("2301.00001")
        assert r.type == "arxiv" and r.value == "2301.00001"

    def test_arxiv_old_format(self):
        r = normalize("hep-ph/0601001")
        assert r.type == "arxiv" and r.value == "hep-ph/0601001"

    def test_arxiv_url(self):
        r = normalize("https://arxiv.org/abs/2301.00001")
        assert r.type == "arxiv" and r.value == "2301.00001"

    def test_arxiv_with_version_stripped(self):
        r = normalize("2301.00001v3")
        assert r.type == "arxiv" and r.value == "2301.00001" and r.version == 3


class TestPMIDNormalization:
    def test_pmid(self):
        r = normalize("PMID:12345678")
        assert r.type == "pmid" and r.value == "12345678"


class TestURLFallback:
    def test_publisher_url(self):
        r = normalize("https://www.nature.com/articles/nature12373")
        assert r.type == "url"


class TestInvalidInput:
    def test_empty_raises(self):
        with pytest.raises(NormalizationError):
            normalize("")

    def test_none_raises(self):
        with pytest.raises(NormalizationError):
            normalize(None)

    def test_garbage_raises(self):
        with pytest.raises(NormalizationError):
            normalize("not an identifier at all random text")

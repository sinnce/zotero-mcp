"""UTS #46 ToASCII as the bridge server's Bun URL parser applies it.

Every expected value below is Bun 1.3.5 ``new URL("https://" + host + "/").hostname``
(``None`` where ``new URL`` throws), captured from the server runtime. The
mirror carries its own Unicode 15.1 tables, so the answers must not depend on
the interpreter's ``unicodedata`` version (3.10 ships 13.0, 3.11 ships 14.0).
"""

import pytest

from zotero_mcp.acquisition import _bridge_idna_tables, bridge_idna

BUN_HOSTNAMES = [
    pytest.param("caf\u00e9.example", "xn--caf-dma.example", id="punycode-encode"),
    pytest.param("stra\u00dfe.example", "xn--strae-oqa.example", id="deviation-nontransitional"),
    pytest.param("\uff30\uff35\uff22.example", "pub.example", id="mapped-fullwidth"),
    pytest.param("\u212a.example", "k.example", id="mapped-kelvin-sign"),
    pytest.param("pub\u00adlisher.example", "publisher.example", id="ignored-soft-hyphen"),
    pytest.param("a\u3002b.example", "a.b.example", id="ideographic-full-stop"),
    pytest.param("xn--caf-dma.example", "xn--caf-dma.example", id="ace-label-kept"),
    pytest.param("XN--CAF-DMA.example", "xn--caf-dma.example", id="ace-label-lowercased"),
    pytest.param("a.xn--caf-dma", "a.xn--caf-dma", id="ace-top-label"),
    pytest.param("xn--ls8h.example", "xn--ls8h.example", id="ace-astral"),
    pytest.param("\U0001f4a9.example", "xn--ls8h.example", id="astral-encode"),
    pytest.param("e\u0301.example", "xn--9ca.example", id="nfc-compose"),
    pytest.param("\u1100\u1161.example", "xn--o39a.example", id="nfc-hangul"),
    pytest.param("\u05d01.example", "xn--1-zhc.example", id="bidi-rtl-then-digit"),
    pytest.param("\u0625\u0646.example", "xn--kgb0e.example", id="bidi-arabic"),
    pytest.param("\u0915\u094d\u200d\u0937.example", "xn--11b2ezcw70k.example", id="contextj-zwj-after-virama"),
    pytest.param("\u00e9" * 60 + ".example", "xn--9ca" + "a" * 59 + ".example", id="label-over-63-allowed"),
    pytest.param("\U00031350.example", "xn--8o8n.example", id="unicode-15.0-code-point"),
    pytest.param("xn--zz.example", None, id="ace-undecodable"),
    pytest.param("xn--.example", None, id="ace-empty"),
    pytest.param("xn--a-.example", None, id="ace-trailing-hyphen"),
    pytest.param("xn--caf-dma\u00e9.example", None, id="ace-with-non-ascii"),
    pytest.param("1\u05d0.example", None, id="bidi-digit-first"),
    pytest.param("\u05d0.1\u05d0.example", None, id="bidi-rtl-domain-digit-label"),
    pytest.param("a\u200db.example", None, id="contextj-zwj-without-virama"),
    pytest.param("\u00df\u200c.example", None, id="contextj-zwnj-without-context"),
    pytest.param("a\u2028b.example", None, id="disallowed-line-separator"),
    pytest.param("\ufffd.example", None, id="disallowed-replacement-character"),
    pytest.param("\u0301a.example", None, id="leading-combining-mark"),
    pytest.param("\u31ef.example", None, id="unicode-15.1-disallowed"),
    pytest.param("\u2ffc.example", None, id="unicode-15.1-ideographic-description"),
    pytest.param("\u00e9" * 1001 + ".example", None, id="punycode-input-over-1000-units"),
]


@pytest.mark.parametrize(("domain", "hostname"), BUN_HOSTNAMES)
def test_to_ascii_matches_bun(domain, hostname):
    assert bridge_idna.to_ascii(domain) == hostname


def test_tables_are_unicode_15_1():
    # ICU 75.1 in Bun 1.3.5 implements Unicode 15.1.
    assert _bridge_idna_tables.UNICODE_VERSION == "15.1.0"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("e\u0301", "\u00e9"),
        ("\u1100\u1161\u11a8", "\uac01"),
        ("\u0958", "\u0915\u093c"),  # composition exclusion stays decomposed
        ("a\u0323\u0302", "\u1ead"),  # canonical reordering, then composition
        ("\u212b", "\u00c5"),  # singleton decomposition
    ],
)
def test_nfc_known_answers(value, expected):
    assert bridge_idna.nfc(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("caf-dma", "caf\u00e9"),
        ("ls8h", "\U0001f4a9"),
        ("zz", None),
        ("99999999999", None),  # int32 overflow
    ],
)
def test_punycode_decode_known_answers(value, expected):
    assert bridge_idna.punycode_decode(value) == expected

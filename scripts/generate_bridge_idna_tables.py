"""Generate ``src/zotero_mcp/acquisition/_bridge_idna_tables.py``.

The browser bridge server parses URLs with Bun's ``new URL()``, which runs
UTS #46 ToASCII through ICU 75.1 (Unicode 15.1). The client mirror in
``bridge_idna.py`` must not depend on the running interpreter's
``unicodedata`` version (Python 3.10 ships Unicode 13.0, 3.11 ships 14.0), so
the Unicode 15.1 data it needs is vendored:

* the UTS #46 15.1.0 IDNA mapping table, read from an ``idna`` package
  ``uts46data.py`` generated for 15.1.0 (idna 3.6), reduced to what
  non-transitional processing without STD3 rules needs;
* Bidi_Class, General_Category=Mark, Canonical_Combining_Class and canonical
  decompositions from the Unicode 15.1.0 character database, read from
  ``unicodedata`` of an interpreter built with it (CPython 3.13);
* Joining_Type, read from the same ``idna`` package's ``idnadata.py``.

Usage::

    uv run --offline python scripts/generate_bridge_idna_tables.py \
        --idna-dir /usr/lib/python3/dist-packages/idna \
        --out src/zotero_mcp/acquisition/_bridge_idna_tables.py
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import sys
import unicodedata
from pathlib import Path

UNICODE_VERSION = "15.1.0"
MAX_CODE_POINT = 0x10FFFF


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _mapping_rows(uts46data) -> list[tuple[int, str | None]]:
    """Range starts with the non-transitional, non-STD3 value of each range.

    ``None`` is valid (valid, deviation, disallowed_STD3_valid), ``""`` is
    ignored, ``"\\ufffd"`` is disallowed (ICU's normalizer maps disallowed
    code points to U+FFFD), anything else is the mapping.
    """
    rows: list[tuple[int, str | None]] = []
    for entry in uts46data.uts46data:
        start, status = entry[0], entry[1]
        mapping = entry[2] if len(entry) > 2 else None
        if status in ("V", "D"):
            value = None
        elif status == "3":
            value = mapping  # None: disallowed_STD3_valid; else disallowed_STD3_mapped
        elif status == "M":
            if not mapping:
                raise SystemExit(f"mapped range without a mapping at U+{start:04X}")
            value = mapping
        elif status == "I":
            value = ""
        elif status == "X":
            value = "\ufffd"
        else:
            raise SystemExit(f"unknown UTS #46 status {status!r} at U+{start:04X}")
        if rows and rows[-1][1] == value and value in (None, "", "\ufffd"):
            continue
        rows.append((start, value))
    if rows[0][0] != 0:
        raise SystemExit("mapping table does not start at U+0000")
    return rows


def _runs(values) -> list[tuple[int, object]]:
    rows: list[tuple[int, object]] = []
    for code_point in range(MAX_CODE_POINT + 1):
        value = values(code_point)
        if not rows or rows[-1][1] != value:
            rows.append((code_point, value))
    return rows


def _canonical_decompositions() -> dict[int, tuple[int, ...]]:
    table: dict[int, tuple[int, ...]] = {}
    for code_point in range(MAX_CODE_POINT + 1):
        if 0xAC00 <= code_point <= 0xD7A3:
            continue  # Hangul syllables decompose algorithmically.
        decomposition = unicodedata.decomposition(chr(code_point))
        if decomposition and not decomposition.startswith("<"):
            table[code_point] = tuple(int(part, 16) for part in decomposition.split())
    return table


def _literal(value: object) -> str:
    if value is None:
        return "None"
    if isinstance(value, str):
        quote = "'" if '"' in value and "'" not in value else '"'
        return quote + "".join(_escape(char, quote) for char in value) + quote
    return repr(value)


def _escape(char: str, quote: str) -> str:
    code_point = ord(char)
    if char in (quote, "\\"):
        return "\\" + char
    if 0x20 <= code_point < 0x7F:
        return char
    if code_point <= 0xFF:
        return f"\\x{code_point:02x}"
    if code_point <= 0xFFFF:
        return f"\\u{code_point:04x}"
    return f"\\U{code_point:08x}"


def _pairs(name: str, comment: str, rows: list[tuple[int, object]]) -> list[str]:
    lines = [f"# {comment}", f"{name} = ("]
    lines += [f"    (0x{start:04X}, {_literal(value)})," for start, value in rows]
    lines.append(")")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--idna-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    if unicodedata.unidata_version != UNICODE_VERSION:
        raise SystemExit(f"unicodedata is {unicodedata.unidata_version}; need {UNICODE_VERSION} (CPython 3.13)")
    uts46_path = args.idna_dir / "uts46data.py"
    idnadata_path = args.idna_dir / "idnadata.py"
    uts46data = _load(uts46_path, "_uts46data")
    idnadata = _load(idnadata_path, "_idnadata")
    for module, path in ((uts46data, uts46_path), (idnadata, idnadata_path)):
        if getattr(module, "__version__", None) != UNICODE_VERSION:
            raise SystemExit(f"{path} is not generated for Unicode {UNICODE_VERSION}")

    mapping = _mapping_rows(uts46data)
    bidi = _runs(lambda cp: unicodedata.bidirectional(chr(cp)))
    marks = _runs(lambda cp: unicodedata.category(chr(cp)).startswith("M"))
    combining = _runs(lambda cp: unicodedata.combining(chr(cp)))
    joining = _runs(lambda cp: chr(idnadata.joining_types.get(cp, ord("U"))))
    decompositions = _canonical_decompositions()
    # A two-code-point canonical decomposition whose code point is not a
    # primary composite (Full_Composition_Exclusion) never recomposes.
    exclusions = sorted(
        code_point
        for code_point, parts in decompositions.items()
        if len(parts) == 2 and unicodedata.normalize("NFC", chr(code_point)) != chr(code_point)
    )

    sources = "\n".join(
        f"#   {path.name} sha256 {hashlib.sha256(path.read_bytes()).hexdigest()}"
        for path in (uts46_path, idnadata_path)
    )
    lines = [
        '"""Unicode 15.1.0 tables for the UTS #46 mirror in ``bridge_idna.py``.',
        "",
        "Generated by ``scripts/generate_bridge_idna_tables.py``; do not edit.",
        '"""',
        "",
        "# Sources: CPython 3.13 unicodedata 15.1.0 and idna 3.6 data files",
        sources,
        "",
        f'UNICODE_VERSION = "{UNICODE_VERSION}"',
        "",
    ]
    lines += _pairs(
        "MAPPING",
        "UTS #46 non-transitional mapping without STD3 rules, as (range start, value):"
        ' None valid, "" ignored, "\\ufffd" disallowed, otherwise the mapping.',
        mapping,
    )
    lines.append("")
    lines += _pairs("BIDI_CLASSES", 'Bidi_Class, as (range start, class); "" is unassigned.', bidi)
    lines.append("")
    lines += _pairs("MARKS", "General_Category=Mark, as (range start, is a mark).", marks)
    lines.append("")
    lines += _pairs("COMBINING_CLASSES", "Canonical_Combining_Class, as (range start, class).", combining)
    lines.append("")
    lines += _pairs("JOINING_TYPES", "Joining_Type, as (range start, type).", joining)
    lines.append("")
    lines.append("# Canonical decomposition mappings (one level), Hangul syllables excluded.")
    lines.append("DECOMPOSITIONS = {")
    lines += [
        f"    0x{code_point:04X}: ({', '.join(f'0x{part:04X}' for part in parts)}{',' if len(parts) == 1 else ''}),"
        for code_point, parts in sorted(decompositions.items())
    ]
    lines.append("}")
    lines.append("")
    lines.append("# Code points with a two-code-point canonical decomposition that NFC does not recompose.")
    lines.append("COMPOSITION_EXCLUSIONS = frozenset(")
    lines.append("    (")
    lines += [f"        0x{code_point:04X}," for code_point in exclusions]
    lines.append("    )")
    lines.append(")")
    args.out.write_text("\n".join(lines) + "\n", encoding="ascii")
    print(
        f"wrote {args.out}: {len(mapping)} mapping rows, {len(bidi)} bidi runs, {len(decompositions)} decompositions",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

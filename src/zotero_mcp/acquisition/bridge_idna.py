"""UTS #46 ToASCII exactly as the bridge server's ``new URL()`` applies it.

The bridge server runs on Bun, whose WHATWG URL parser (WebKit
``URLParser::domainToASCII``) hands a percent-decoded domain that is not
ASCII, or that has a label starting with ``xn--`` in any letter case, to ICU
75.1 ``uidna_nameToASCII`` with ``UIDNA_CHECK_BIDI``, ``UIDNA_CHECK_CONTEXTJ``
and non-transitional processing, and without ``UIDNA_USE_STD3_RULES``. The
host is accepted only when the result is not empty and ICU reports no
error other than EMPTY_LABEL, LABEL_TOO_LONG, DOMAIN_NAME_TOO_LONG,
LEADING_HYPHEN, TRAILING_HYPHEN or HYPHEN_3_4; every other flag
(LEADING_COMBINING_MARK, DISALLOWED, PUNYCODE, LABEL_HAS_DOT,
INVALID_ACE_LABEL, BIDI, CONTEXTJ) fails the URL parse.

This module reproduces ICU's ``UTS46::process`` (``uts46.cpp``) and
``u_strFromPunycode``/``u_strToPunycode`` (``punycode.cpp``) step by step,
over Unicode 15.1 tables vendored in ``_bridge_idna_tables.py`` so the result
does not depend on the interpreter's ``unicodedata`` version. Tolerated error
flags are not tracked.
"""

from __future__ import annotations

from bisect import bisect_right
from typing import Final

from . import _bridge_idna_tables as _tables

UNICODE_VERSION: Final = _tables.UNICODE_VERSION


# u_strToPunycode refuses a label longer than MAX_CP_COUNT UTF-16 code units
# (U_INPUT_TOO_LONG_ERROR), which fails the whole conversion.
_MAX_PUNYCODE_INPUT_UNITS: Final = 1000


def _split_table(rows):
    return tuple(start for start, _ in rows), tuple(value for _, value in rows)


_MAPPING_STARTS, _MAPPING_VALUES = _split_table(_tables.MAPPING)
_BIDI_STARTS, _BIDI_VALUES = _split_table(_tables.BIDI_CLASSES)
_MARK_STARTS, _MARK_VALUES = _split_table(_tables.MARKS)
_CCC_STARTS, _CCC_VALUES = _split_table(_tables.COMBINING_CLASSES)
_JOINING_STARTS, _JOINING_VALUES = _split_table(_tables.JOINING_TYPES)
_COMPOSITIONS: Final[dict[tuple[int, int], int]] = {
    parts: code_point
    for code_point, parts in _tables.DECOMPOSITIONS.items()
    if len(parts) == 2 and code_point not in _tables.COMPOSITION_EXCLUSIONS
}

_S_BASE, _L_BASE, _V_BASE, _T_BASE = 0xAC00, 0x1100, 0x1161, 0x11A7
_L_COUNT, _V_COUNT, _T_COUNT = 19, 21, 28
_N_COUNT = _V_COUNT * _T_COUNT
_S_COUNT = _L_COUNT * _N_COUNT


def _lookup(starts: tuple[int, ...], values: tuple, code_point: int):
    return values[bisect_right(starts, code_point) - 1]


def bidi_class(code_point: int) -> str:
    return _lookup(_BIDI_STARTS, _BIDI_VALUES, code_point)


def is_mark(code_point: int) -> bool:
    return _lookup(_MARK_STARTS, _MARK_VALUES, code_point)


def combining_class(code_point: int) -> int:
    return _lookup(_CCC_STARTS, _CCC_VALUES, code_point)


def joining_type(code_point: int) -> str:
    return _lookup(_JOINING_STARTS, _JOINING_VALUES, code_point)


def uts46_map(value: str) -> str:
    """UTS #46 mapping step: disallowed code points become U+FFFD, as in ICU."""
    output = []
    for char in value:
        mapped = _lookup(_MAPPING_STARTS, _MAPPING_VALUES, ord(char))
        output.append(char if mapped is None else mapped)
    return "".join(output)


def _decompose(code_point: int, output: list[int]) -> None:
    if _S_BASE <= code_point < _S_BASE + _S_COUNT:
        index = code_point - _S_BASE
        output.append(_L_BASE + index // _N_COUNT)
        output.append(_V_BASE + (index % _N_COUNT) // _T_COUNT)
        if index % _T_COUNT:
            output.append(_T_BASE + index % _T_COUNT)
        return
    parts = _tables.DECOMPOSITIONS.get(code_point)
    if parts is None:
        output.append(code_point)
        return
    for part in parts:
        _decompose(part, output)


def _compose_pair(first: int, second: int) -> int | None:
    if _L_BASE <= first < _L_BASE + _L_COUNT and _V_BASE <= second < _V_BASE + _V_COUNT:
        return _S_BASE + ((first - _L_BASE) * _V_COUNT + second - _V_BASE) * _T_COUNT
    if (
        _S_BASE <= first < _S_BASE + _S_COUNT
        and (first - _S_BASE) % _T_COUNT == 0
        and _T_BASE < second < _T_BASE + _T_COUNT
    ):
        return first + second - _T_BASE
    return _COMPOSITIONS.get((first, second))


def nfc(value: str) -> str:
    """Unicode 15.1 NFC (UAX #15) over the vendored tables."""
    code_points: list[int] = []
    for char in value:
        _decompose(ord(char), code_points)
    # Canonical ordering: stable sort of each run of non-starters.
    index = 0
    while index < len(code_points):
        if combining_class(code_points[index]) == 0:
            index += 1
            continue
        end = index
        while end < len(code_points) and combining_class(code_points[end]) != 0:
            end += 1
        code_points[index:end] = sorted(code_points[index:end], key=combining_class)
        index = end
    # Canonical composition.
    output: list[int] = []
    starter: int | None = None
    last_class = 0
    for code_point in code_points:
        klass = combining_class(code_point)
        adjacent = starter is not None and len(output) - 1 == starter
        if starter is not None and (adjacent or (last_class != 0 and last_class < klass)):
            composite = _compose_pair(output[starter], code_point)
            if composite is not None:
                output[starter] = composite
                continue
        if klass == 0:
            starter = len(output)
        last_class = klass
        output.append(code_point)
    return "".join(map(chr, output))


def uts46_normalize(value: str) -> str:
    """ICU's ``uts46`` Normalizer2: the UTS #46 mapping followed by NFC."""
    return nfc(uts46_map(value))


_PUNY_BASE, _PUNY_TMIN, _PUNY_TMAX, _PUNY_SKEW, _PUNY_DAMP = 36, 1, 26, 38, 700
_PUNY_INITIAL_BIAS, _PUNY_INITIAL_N = 72, 0x80
_INT32_MAX: Final = 0x7FFFFFFF


def _punycode_digit(char: str) -> int:
    if "0" <= char <= "9":
        return ord(char) - ord("0") + 26
    if "a" <= char <= "z":
        return ord(char) - ord("a")
    if "A" <= char <= "Z":
        return ord(char) - ord("A")
    return -1


def _adapt_bias(delta: int, length: int, first_time: bool) -> int:
    delta = delta // _PUNY_DAMP if first_time else delta // 2
    delta += delta // length
    count = 0
    while delta > ((_PUNY_BASE - _PUNY_TMIN) * _PUNY_TMAX) // 2:
        delta //= _PUNY_BASE - _PUNY_TMIN
        count += _PUNY_BASE
    return count + ((_PUNY_BASE - _PUNY_TMIN + 1) * delta) // (delta + _PUNY_SKEW)


def punycode_decode(value: str) -> str | None:
    """``u_strFromPunycode``: ``None`` where ICU reports an error.

    The basic code points are those before the last delimiter; a delimiter at
    index 0 leaves no basic code points and is then read as a (bad) digit.
    Arithmetic is bounded by int32 like ICU's.
    """
    basic_length = max(value.rfind("-"), 0)
    output = []
    for char in value[:basic_length]:
        if ord(char) >= 0x80:
            return None
        output.append(char)
    n, i, bias = _PUNY_INITIAL_N, 0, _PUNY_INITIAL_BIAS
    position = basic_length + 1 if basic_length > 0 else 0
    while position < len(value):
        old_i, weight, k = i, 1, _PUNY_BASE
        while True:
            if position >= len(value):
                return None
            digit = _punycode_digit(value[position])
            position += 1
            if digit < 0:
                return None
            if digit > (_INT32_MAX - i) // weight:
                return None
            i += digit * weight
            if k <= bias:
                threshold = _PUNY_TMIN
            elif k >= bias + _PUNY_TMAX:
                threshold = _PUNY_TMAX
            else:
                threshold = k - bias
            if digit < threshold:
                break
            if weight > _INT32_MAX // (_PUNY_BASE - threshold):
                return None
            weight *= _PUNY_BASE - threshold
            k += _PUNY_BASE
        count = len(output) + 1
        bias = _adapt_bias(i - old_i, count, old_i == 0)
        if i // count > _INT32_MAX - n:
            return None
        n += i // count
        i %= count
        if n > 0x10FFFF or 0xD800 <= n <= 0xDFFF:
            return None
        output.insert(i, chr(n))
        i += 1
    return "".join(output)


def _utf16_length(value: str) -> int:
    return len(value) + sum(1 for char in value if ord(char) > 0xFFFF)


def _check_label_bidi(label: str) -> tuple[bool, bool]:
    """ICU ``checkLabelBiDi`` (RFC 5893 rules 1-6): (label is ok, label is RTL)."""
    first = bidi_class(ord(label[0]))
    ok = first in ("L", "R", "AL")
    last = first
    end = len(label)
    while end > 1:
        end -= 1
        last = bidi_class(ord(label[end]))
        if last != "NSM":
            break
    else:
        last = first
    if first == "L":
        if last not in ("L", "EN"):
            ok = False
    elif last not in ("R", "AL", "EN", "AN"):
        ok = False
    classes = {first, last} | {bidi_class(ord(char)) for char in label[1:end]}
    if first == "L":
        if not classes <= {"L", "EN", "ES", "CS", "ET", "ON", "BN", "NSM"}:
            ok = False
    else:
        if not classes <= {"R", "AL", "AN", "EN", "ES", "CS", "ET", "ON", "BN", "NSM"}:
            ok = False
        if "EN" in classes and "AN" in classes:
            ok = False
    return ok, bool(classes & {"R", "AL", "AN"})


def _is_ascii_ok_bidi(prefix: str) -> bool:
    """ICU ``isASCIIOkBiDi`` for the labels its ASCII fast path already copied."""
    label_start = 0
    for index, char in enumerate(prefix):
        if char == ".":
            if index > label_start and not ("a" <= prefix[index - 1] <= "z" or "0" <= prefix[index - 1] <= "9"):
                return False
            label_start = index + 1
        elif index == label_start:
            if not "a" <= char <= "z":
                return False
        elif char <= " " and (char >= "\x1c" or "\t" <= char <= "\r"):
            return False
    return True


def _is_label_ok_context_j(label: str) -> bool:
    """ICU ``isLabelOkContextJ`` (RFC 5892 Appendix A.1 and A.2)."""
    for index, char in enumerate(label):
        if char == "\u200c":
            if index == 0:
                return False
            before = index - 1
            if combining_class(ord(label[before])) == 9:
                continue
            while True:
                kind = joining_type(ord(label[before]))
                if kind == "T":
                    if before == 0:
                        return False
                    before -= 1
                elif kind in ("L", "D"):
                    break
                else:
                    return False
            after = index + 1
            while True:
                if after == len(label):
                    return False
                kind = joining_type(ord(label[after]))
                after += 1
                if kind in ("R", "D"):
                    break
                if kind != "T":
                    return False
        elif char == "\u200d":
            if index == 0 or combining_class(ord(label[index - 1])) != 9:
                return False
    return True


class _Conversion:
    def __init__(self) -> None:
        self.failed = False
        self.is_bidi = False
        self.is_ok_bidi = True

    def process_label(self, label: str) -> str:
        """ICU ``processLabel`` for ToASCII; sets ``failed`` on a fatal error."""
        was_punycode = False
        work = label
        if label.startswith("xn--"):
            if len(label) == 4 or (len(label) > 5 and label.endswith("-")):
                self.failed = True  # INVALID_ACE_LABEL
                return label
            decoded = punycode_decode(label[4:])
            if decoded is None or uts46_normalize(decoded) != decoded:
                self.failed = True  # PUNYCODE / INVALID_ACE_LABEL
                return label
            was_punycode = True
            work = decoded
        if work == "":
            return work  # EMPTY_LABEL
        if "." in work or "\ufffd" in work or is_mark(ord(work[0])):
            self.failed = True  # LABEL_HAS_DOT / DISALLOWED / LEADING_COMBINING_MARK
            return label
        ok, is_rtl = _check_label_bidi(work)
        self.is_ok_bidi = self.is_ok_bidi and ok
        self.is_bidi = self.is_bidi or is_rtl
        if ("\u200c" in work or "\u200d" in work) and not _is_label_ok_context_j(work):
            self.failed = True  # CONTEXTJ
            return label
        if was_punycode:
            return label
        if work.isascii():
            return work
        if _utf16_length(work) > _MAX_PUNYCODE_INPUT_UNITS:
            self.failed = True
            return label
        return "xn--" + work.encode("punycode").decode("ascii")


def to_ascii(domain: str) -> str | None:
    """``uidna_nameToASCII`` as WebKit uses it; ``None`` means ``new URL()`` throws."""
    # ICU's ASCII fast path copies (and lowercases) ASCII up to the first
    # non-ASCII code point or the "-" of a label starting "??--".
    label_start = index = 0
    while index < len(domain):
        char = domain[index]
        if char > "\x7f":
            break
        if char == "-" and index == label_start + 3 and domain[index - 1] == "-":
            index += 1
            break
        if char == ".":
            label_start = index + 1
        index += 1
    else:
        return domain.lower() or None
    normalized = uts46_normalize(domain)
    conversion = _Conversion()
    output = [normalized[:label_start]]
    labels = normalized[label_start:].split(".")
    for position, label in enumerate(labels):
        if position:
            output.append(".")
        is_last = position == len(labels) - 1
        if not is_last or label != "" or (label_start == 0 and len(labels) == 1):
            output.append(conversion.process_label(label))
        if conversion.failed:
            return None
    if conversion.is_bidi and (
        not conversion.is_ok_bidi or (label_start > 0 and not _is_ascii_ok_bidi(normalized[:label_start]))
    ):
        return None  # BIDI
    return "".join(output) or None

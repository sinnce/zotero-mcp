"""Conservative, linear-time cleaning for a deliberate embedding rebuild.

Rules inspect original line boundaries before whitespace is collapsed. Delimiter
scans either consume their input or stop at the next opener; no suffix is scanned
repeatedly. Encoded markup and nested entities remain literal to keep the output
stable under repeated entity decoding; ordinary entities decode after tag removal.

clean_v2 revision (unreleased): bound generic chrome to banner context, remove
only stamp-contributing lines, pair HTML locally, and require header regions.
Classification uses source boundaries: re-cleaning collapsed text cannot recover
those boundaries (for example, I\nUnderstand becomes a single banner-like line).
"""

import html
import re
from collections import Counter

CLEAN_VERSION = "clean_v2"

_STAMP = re.compile(r"arxiv:\d{4}\.\d{4,5}(?:v\d+)?", re.I)
_BANNER = re.compile(
    r"(?:change to arxiv['’]s privacy policy[.!]?|"
    r"the arxiv privacy policy has changed\b.*|"
    r"i understand|skip to main content|open (?:search|navigation menu))", re.I,
)
_CHROME_RADIUS = 5
_CHROME = re.compile(
    r"(?:"
    r"(?:we have updated|please review) (?:our |arxiv['’]s )privacy policy[.!]?|"
    r"skip to yearly menu bar|"
    r"open (?:search|navigation menu)|arxiv logo|cornell university|"
    r"main navigation|search|advanced search|help|about|donate|menu|"
    r"close (?:search|navigation menu))", re.I,
)
# Labels exclude open brackets; destinations exclude all markdown delimiters.
# Failed candidates cannot rescan a later candidate's label or destination.
_IMAGE = re.compile(r"!\[([^\[\]\n]{0,256})\]\(([^()\[\]\n]*)\)")
_LINK = re.compile(r"\[([^\[\]\n]{0,256})\]\(([^()\[\]\n]*)\)")
_ARXIV_PRIVACY = re.compile(
    r"The \[arXiv Privacy Policy\]\(https://info\.arxiv\.org/help/policies/privacy_policy\.html\) "
    r"has changed\. By continuing to use arxiv\.org, you are agreeing to the privacy policy\.", re.I,
)
_PRIVACY_URL = re.compile(
    r"https://(?:info\.)?arxiv\.org/help/policies/privacy_policy(?:\.html)?/?", re.I,
)
_HEADER = re.compile(
    r"(?:research article\s*[|\u2003]|open access$|"
    r"(?:pnas|science|nature(?: communications)?|plos one)\s*[|•]|"
    r"page \d+ of \d+$|\d+\s*[|•]\s*(?:pnas|science|nature)\b)", re.I,
)
_TAG_NAMES = frozenset(
    "br p div span a i b em strong sup sub ul ol li table tr td th "
    "h1 h2 h3 h4 h5 h6 img html body head meta link hr script style".split()
)
_VOID_TAGS = frozenset({"br", "img", "meta", "link", "hr"})
_TAG_START = re.compile(r"<(/?)([a-z][a-z0-9]{0,5})(?=[\s/><]|$)", re.I)
_PARAGRAPH_BREAK = re.compile(r"\r?\n[ \t\r]*\n|\f")
_RAW_CLOSE = {name: re.compile(r"</" + name + r"\s*>", re.I) for name in ("script", "style")}
_HTML_TOKEN = re.compile(r"[<>]|&(?:#[xX][0-9a-fA-F]{1,8}|#\d{1,10}|[a-zA-Z][a-zA-Z0-9]{0,31});")
_ENTITY_TAIL = re.compile(r"(?:#[xX][0-9a-fA-F]{1,8}|#\d{1,10}|[a-zA-Z][a-zA-Z0-9]{0,31});")


def drop_rotated_watermark_runs(text: str) -> str:
    """Drop >=8 short lines only with a forward or reversed arXiv stamp."""
    lines = text.split("\n")
    kept = []
    i = 0
    while i < len(lines):
        j = i
        while j < len(lines) and len(lines[j].strip()) <= 1:
            j += 1
        if j > i:
            cells = [(line.strip(), k) for k, line in enumerate(lines[i:j], i) if line.strip()]
            chars = "".join(char for char, _ in cells)
            removed = set()
            if j - i >= 8:
                for candidate, mapping in ((chars, cells), (chars[::-1], cells[::-1])):
                    for match in _STAMP.finditer(candidate):
                        removed.update(k for _, k in mapping[match.start():match.end()])
            kept.extend(lines[k] for k in range(i, j) if k not in removed)
            i = j
        else:
            kept.append(lines[i])
            i += 1
    return "\n".join(kept)


def _chrome_only(line: str, *, in_block: bool = False) -> bool:
    visible = line.strip().strip("#* \t")
    if _ARXIV_PRIVACY.fullmatch(visible):
        return True
    link = _LINK.fullmatch(visible)
    if link:
        visible = link[1].strip()
        if link[2].casefold().startswith("data:image"):
            return True
        if in_block and (
            re.fullmatch(r"(?:arxiv(?: logo)?|(?:cornell university )?logo)", visible, re.I)
            or visible.casefold() == "privacy policy" and _PRIVACY_URL.fullmatch(link[2])
        ):
            return True
    if _BANNER.fullmatch(visible) or in_block and _CHROME.fullmatch(visible):
        return True
    image = _IMAGE.fullmatch(visible)
    if image:
        return bool(
            re.search(r"\b(?:logo|arxiv)\b", image[1], re.I)
            or image[2].casefold().startswith("data:image")
        )
    # A linked image is common on the arXiv page. Test the entire wrapper.
    if visible.startswith("[!"):
        inner = _IMAGE.match(visible, 1)
        if inner and visible[inner.end():].startswith("]("):
            destination = visible[inner.end() + 2:]
            if destination.endswith(")") and not any(c in destination[:-1] for c in "()[]"):
                return _chrome_only(inner[0], in_block=in_block)
    return False


def drop_web_chrome_lines(text: str) -> str:
    """Remove distinctive chrome; generic lines need a banner within 5 lines."""
    lines = text.split("\n")
    # Only textual banners establish arXiv context, not arbitrary logo images.
    banners = [i for i, line in enumerate(lines) if (
        _BANNER.fullmatch(line.strip().strip("#* \t"))
        or _ARXIV_PRIVACY.fullmatch(line.strip().strip("#* \t"))
    )]
    in_block = set()
    for i in banners:
        in_block.update(range(max(0, i - _CHROME_RADIUS), min(len(lines), i + _CHROME_RADIUS + 1)))
    kept = []
    for i, line in enumerate(lines):
        if _chrome_only(line, in_block=i in in_block):
            continue
        kept.append(_IMAGE.sub(
            lambda m: " " if m[2].casefold().startswith("data:image") else m[0], line,
        ))
    return "\n".join(kept)


def drop_running_headers(text: str) -> str:
    """Require >=3 repetitions plus a page region or distinctive journal header.

    Only occurrences at a boundary are removed for ordinary header patterns.
    Blank paragraph breaks alone do not establish a page boundary.
    """
    lines = text.replace("\f", "\n\f\n").split("\n")
    normalized = [collapse_whitespace(line).casefold() for line in lines]
    counts = Counter(normalized)
    boundaries = {i for i, line in enumerate(lines) if (
        line == "\f" or re.fullmatch(r"\s*<!--\s*page\s*break\s*-->\s*", line, re.I)
    )}
    # The first line belongs to a page-like segment only when actual page
    # boundaries exist. A table that starts the document is not page evidence.
    regions = {next((i for i, key in enumerate(normalized) if key), None)} if boundaries else set()
    previous = None
    after_boundary = False
    for i, line in enumerate(lines):
        if i in boundaries:
            regions.add(previous)
            after_boundary = True
        elif line.strip():
            if after_boundary:
                regions.add(i)
            after_boundary = False
            previous = i
    kept = []
    for i, (line, key) in enumerate(zip(lines, normalized)):
        region = i in regions
        distinctive = re.match(r"research article\s*[|\u2003]", line.strip(), re.I)
        if counts[key] >= 3 and (_HEADER.match(key) or _HEADER.match(line.strip())) and (region or distinctive):
            continue
        kept.append(line)
    return "\n".join(kept).rstrip("\n")


def _strip_paragraph_tags(text: str) -> str:
    """Scan allowlisted tags once, respecting quoted attribute delimiters.

    An unclosed tag stops at another '<', so adversarial openers are linear.
    Only locally paired allowlisted tags and known void tags are markup.
    """
    tokens = []
    stacks = {}
    paired = set()
    raw_closes = {name: iter(pattern.finditer(text)) for name, pattern in _RAW_CLOSE.items()}
    next_close = {name: next(matches, None) for name, matches in raw_closes.items()}
    i = 0
    while i < len(text):
        if text[i] != "<":
            i += 1
            continue
        start = _TAG_START.match(text, i)
        if start is None or start[2].casefold() not in _TAG_NAMES:
            i += 1
            continue
        j = start.end()
        quote = None
        while j < len(text):
            char = text[j]
            if char == "<" and quote is None:
                break
            if quote:
                if char == quote:
                    quote = None
            elif char in "\"'":
                quote = char
            elif char == ">":
                break
            j += 1
        if j == len(text) or text[j] != ">":
            tokens.append((i, j, None, False))
            i = j
            continue
        name = start[2].casefold()
        index = len(tokens)
        tokens.append((i, j + 1, name, bool(start[1])))
        if name in raw_closes and not start[1]:
            closing = next_close[name]
            while closing is not None and closing.start() < j + 1:
                closing = next(raw_closes[name], None)
            next_close[name] = closing
            if closing is not None:
                tokens.append((closing.start(), closing.end(), name, True))
                paired.update((index, index + 1))
                i = closing.end()
                continue
        if name in _VOID_TAGS:
            paired.add(index)
        elif start[1]:
            stack = stacks.get(name)
            if stack:
                paired.update((stack.pop(), index))
        else:
            stacks.setdefault(name, []).append(index)
        i = j + 1
    out = []
    end = 0
    raw = None
    for index, (a, b, name, closing) in enumerate(tokens):
        if raw is not None:
            if name == raw and closing and index in paired:
                out.append(" ")
                raw = None
            end = b
            continue
        out.append(text[end:a])
        if name is None:
            out.append("&lt;" + text[a + 1:b])
        elif index in paired:
            out.append(" ")
            if name in {"script", "style"} and not closing:
                raw = name
        else:
            out.append(text[a:b])
        end = b
    out.append(text[end:])
    return "".join(out)


def _strip_tags(text: str) -> str:
    return "".join(
        part if i % 2 else _strip_paragraph_tags(part)
        for i, part in enumerate(re.split("(" + _PARAGRAPH_BREAK.pattern + ")", text))
    )


def strip_html(text: str) -> str:
    """Strip real markup first, then decode ordinary entities only once.

    Preserve angle-bracket and nested ampersand entities: decoding them can create
    markup or another entity on the next pass. This conservative exception is
    necessary for idempotence without recursively decoding literal source text.
    """
    return _decode_entities(_strip_tags(text))


def _decode_entities(text: str) -> str:
    in_literal_angle = False

    def decode(match: re.Match[str]) -> str:
        nonlocal in_literal_angle
        if match[0] in {"<", ">"}:
            in_literal_angle = match[0] == "<"
            return match[0]
        # An entity inside literal angle notation must not create a tag name
        # or attribute boundary on the next pass (e.g. <&#97;>).
        if in_literal_angle:
            return match[0]
        value = html.unescape(match[0])
        # Bounded lookahead also catches entities assembled from adjacent
        # references, such as &amp;&#97;mp; -> &amp;.
        nested = value == "&" and _ENTITY_TAIL.match(html.unescape(text[match.end():match.end() + 512]))
        if value in {"<", ">"} or nested:
            return match[0]
        return value

    return _HTML_TOKEN.sub(decode, text)


def collapse_whitespace(text: str) -> str:
    """Collapse whitespace to single spaces and trim the ends."""
    return re.sub(r"\s+", " ", text).strip()


def clean_embedded_text(text: str) -> str:
    """Apply clean_v2 before the character cap; trim after the cap too."""
    text = drop_rotated_watermark_runs(text)
    text = drop_web_chrome_lines(text)
    text = drop_running_headers(text)
    text = strip_html(text)
    text = collapse_whitespace(text)
    if len(text) > 8000:
        # Cutting a literal <beta> to <b, or a protected &amp;lt; to &amp;,
        # must not leave fresh markup/entities for the next invocation.
        # Previously protected references can now decode once if safe.
        capped = text[:8000].rstrip()
        last_open = capped.rfind("<")
        candidate = _TAG_START.match(capped, last_open) if last_open >= 0 else None
        if candidate and candidate[2].casefold() in _TAG_NAMES and ">" not in capped[last_open:]:
            # Drop only the fragment cut by the cap, rather than leaving a
            # partially emitted escape sequence in the embedding text.
            capped = capped[:last_open].rstrip()
        # Do not reclassify literal tags after source paragraphs have collapsed.
        text = collapse_whitespace(_decode_entities(capped))[:8000].rstrip()
    return text

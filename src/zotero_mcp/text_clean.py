"""Conservative, linear-time cleaning for a deliberate embedding rebuild.

Rules inspect original line boundaries before whitespace is collapsed. Delimiter
scans either consume their input or stop at the next opener; no suffix is scanned
repeatedly. Encoded markup and nested entities remain literal to keep the output
stable under repeated cleaning; ordinary entities are decoded after tag removal.
"""

import html
import re
from collections import Counter

CLEAN_VERSION = "clean_v2"

_STAMP = re.compile(r"arxiv:\d{4}\.\d{4,5}(?:v\d+)?", re.I)
_CHROME = re.compile(
    r"(?:change to arxiv['’]s privacy policy[.!]?|"
    r"(?:we have updated|please review) (?:our |arxiv['’]s )privacy policy[.!]?|"
    r"i understand|skip to (?:main content|yearly menu bar)|"
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
    "h1 h2 h3 h4 h5 h6 img html body head meta link script style".split()
)
_TAG_START = re.compile(r"<(/?)([a-z][a-z0-9]{0,5})(?=[\s/><]|$)", re.I)
_CLOSING_TAG = re.compile(r"</([a-z][a-z0-9]{0,5})\s*>", re.I)
_RAW_CLOSE = {name: re.compile(r"</" + name + r"\s*>", re.I) for name in ("script", "style")}
_HTML_TOKEN = re.compile(r"[<>]|&(?:#[xX][0-9a-fA-F]{1,8}|#\d{1,10}|[a-zA-Z][a-zA-Z0-9]{0,31});")
_ENTITY_TAIL = re.compile(r"(?:#[xX][0-9a-fA-F]{1,8}|#\d{1,10}|[a-zA-Z][a-zA-Z0-9]{0,31});")


def drop_rotated_watermark_runs(text: str) -> str:
    """Drop >=8 short lines only with a forward or reversed arXiv stamp."""
    lines = text.splitlines()
    kept = []
    i = 0
    while i < len(lines):
        j = i
        while j < len(lines) and len(lines[j].strip()) <= 1:
            j += 1
        if j > i:
            chars = "".join(lines[i:j])
            chars = "".join(chars.split())
            if j - i < 8 or not (_STAMP.search(chars) or _STAMP.search(chars[::-1])):
                kept.extend(lines[i:j])
            i = j
        else:
            kept.append(lines[i])
            i += 1
    return "\n".join(kept)


def _chrome_only(line: str) -> bool:
    visible = line.strip().strip("#* \t")
    if _ARXIV_PRIVACY.fullmatch(visible):
        return True
    link = _LINK.fullmatch(visible)
    if link:
        visible = link[1].strip()
        if link[2].casefold().startswith("data:image"):
            return True
        if re.fullmatch(r"(?:arxiv(?: logo)?|(?:cornell university )?logo)", visible, re.I):
            return True
        if visible.casefold() == "privacy policy" and _PRIVACY_URL.fullmatch(link[2]):
            return True
    if _CHROME.fullmatch(visible):
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
                return _chrome_only(inner[0])
    return False


def drop_web_chrome_lines(text: str) -> str:
    """Remove verified chrome-only lines and inline data-image tokens."""
    kept = []
    for line in text.splitlines():
        if _chrome_only(line):
            continue
        kept.append(_IMAGE.sub(
            lambda m: " " if m[2].casefold().startswith("data:image") else m[0], line,
        ))
    return "\n".join(kept)


def drop_running_headers(text: str) -> str:
    """Require both a header pattern and >=3 identical normalized lines."""
    lines = text.splitlines()
    normalized = [collapse_whitespace(line).casefold() for line in lines]
    counts = Counter(normalized)
    return "\n".join(
        line for line, key in zip(lines, normalized)
        if counts[key] < 3 or not (_HEADER.match(key) or _HEADER.match(line.strip()))
    )


def _strip_tags(text: str) -> str:
    """Scan allowlisted tags once, respecting quoted attribute delimiters.

    An unclosed tag stops at another '<', so adversarial openers are linear.
    Script/style contents are suppressed until their corresponding closing tag;
    an unclosed raw-text block is suppressed through the end of the input.
    """
    out = []
    closing_names = {match[1].casefold() for match in _CLOSING_TAG.finditer(text)}
    i = 0
    raw = None
    while i < len(text):
        if raw is not None:
            # Raw-text blocks end at their closing tag even when their content
            # contains tag-like strings with unmatched attribute quotes.
            closing = _RAW_CLOSE[raw].search(text, i)
            if closing is None:
                break
            out.append(" ")
            i = closing.end()
            raw = None
            continue
        if text[i] != "<":
            out.append(text[i])
            i += 1
            continue
        start = _TAG_START.match(text, i)
        if start is None or start[2].casefold() not in _TAG_NAMES:
            out.append("<")
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
            # Preserve literal meaning without letting later removals turn
            # this malformed opener into a new tag on the next pass.
            out.append("&lt;" + text[i + 1:j])
            i = j
            continue
        name = start[2].casefold()
        # A bare, unpaired formatting name between operands can be literal
        # notation (a<b>c). Paired markup and void elements remain recognized.
        if (
            not start[1] and j == start.end()
            and name in {"a", "b", "i", "em", "strong", "sup", "sub"}
            and name not in closing_names and i > 0 and j + 1 < len(text)
            and text[i - 1].isalnum() and text[j + 1].isalnum()
        ):
            out.append(text[i:j + 1])
            i = j + 1
            continue
        out.append(" ")
        if name in {"script", "style"} and not start[1] and text[j - 1] != "/":
            raw = name
        i = j + 1
    return "".join(out)


def strip_html(text: str) -> str:
    """Strip real markup first, then decode ordinary entities only once.

    Preserve angle-bracket and nested ampersand entities: decoding them can create
    markup or another entity on the next pass. This conservative exception is
    necessary for idempotence without recursively decoding literal source text.
    """
    text = _strip_tags(text)
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
        text = collapse_whitespace(strip_html(capped))[:8000].rstrip()
    # Normalization can itself expose a chrome-only document (e.g. I\nUnderstand).
    return "" if _chrome_only(text) else text

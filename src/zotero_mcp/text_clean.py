"""Pure, ordered port of the measured stored-text clean_v1 recipe.

Keep the rule order and 8,000-character cap aligned with the shadow experiment.
This intentionally uses the reference's broad HTML and chrome heuristics.
Like the reference, it is not universally idempotent: truncation can leave a
trailing space, and nested entities can decode further on a second pass.
Apply once to original source text.
"""

import html
import re

CLEAN_VERSION = "clean_v1"


def drop_rotated_watermark_runs(text: str) -> str:
    """Drop runs of at least five lines whose stripped length is at most one."""
    lines = text.splitlines()
    kept = []
    i = 0
    while i < len(lines):
        if len(lines[i].strip()) <= 1:
            j = i + 1
            while j < len(lines) and len(lines[j].strip()) <= 1:
                j += 1
            if j - i >= 5:
                i = j
                continue
        kept.append(lines[i])
        i += 1
    return "\n".join(kept)


def drop_web_chrome_lines(text: str) -> str:
    """Drop the reference's complete navigation, privacy, and logo lines."""
    kept = []
    for line in text.splitlines():
        if re.search(
            r'data:image|!\[[^\]]*(?:logo|arxiv)[^\]]*\]|privacy policy|skip to (?:main content|yearly menu bar)|open (?:search|navigation menu)',
            line, re.I,
        ):
            continue
        visible = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', line).strip(' #*\t')
        if re.fullmatch(
            r'(?:arxiv logo|cornell university|main navigation|search|advanced search|help|about|donate|menu|close (?:search|navigation menu))',
            visible, re.I,
        ):
            continue
        kept.append(line)
    return "\n".join(kept)


def drop_running_headers(text: str) -> str:
    """Keep the first research-article header; drop repeats and OPEN ACCESS."""
    kept = []
    seen_headers = set()
    for line in text.splitlines():
        if re.match(r'^\s*(?:RESEARCH ARTICLE\s*\||OPEN ACCESS\b)', line, re.I):
            header = re.sub(r'\s+', ' ', line).strip().casefold()
            if header in seen_headers or re.fullmatch(r'open access', header):
                continue
            seen_headers.add(header)
        kept.append(line)
    return "\n".join(kept)


def strip_html(text: str) -> str:
    """Decode entities once, then replace tags with spaces, as in clean_v1."""
    return re.sub(r'<[^>]*>', ' ', html.unescape(text))


def collapse_whitespace(text: str) -> str:
    """Collapse whitespace to single spaces and trim the ends."""
    return re.sub(r'\s+', ' ', text).strip()


def clean_embedded_text(text: str) -> str:
    """Apply clean_v1 without reconstructing fields or changing their order."""
    text = drop_rotated_watermark_runs(text)
    text = drop_web_chrome_lines(text)
    text = drop_running_headers(text)
    text = strip_html(text)
    return collapse_whitespace(text)[:8000]

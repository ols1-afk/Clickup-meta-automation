"""Turn a ClickUp task description into clean Meta primary text.

Meta's primary text box is plain text. Pasting rich text into it tends to
collapse line breaks or leave stray markdown, so we rebuild the text from
scratch: every non-empty line becomes its own paragraph, separated by exactly
one blank line.
"""

import re

DRIVE_URL_RE = re.compile(r"https?://(?:drive|docs)\.google\.com/\S+", re.IGNORECASE)

# [label](https://example.com) -> handled by _replace_link
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\((https?://[^)\s]+)\)")
# Autolinks: <https://example.com>
_MD_AUTOLINK_RE = re.compile(r"<(https?://[^>\s]+)>")
# Markdown emphasis markers that Meta would show literally.
_MD_BOLD_RE = re.compile(r"(\*\*|__)(.+?)\1")
_MD_STRIKE_RE = re.compile(r"~~(.+?)~~")
_MD_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+")
_MD_QUOTE_RE = re.compile(r"^\s*>\s?")
# ClickUp escapes punctuation in markdown output, e.g. "Save 20%\!".
_MD_ESCAPE_RE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|>~])")

_INVISIBLE_RE = re.compile(r"[​‌‍⁠﻿]")
_SPACES_RE = re.compile(r"[ \t   ]+")


def _replace_link(match: re.Match) -> str:
    label, url = match.group(1).strip(), match.group(2)
    bare = re.sub(r"^https?://(www\.)?", "", url).rstrip("/")
    if not label or label == url or label.rstrip("/") in (url.rstrip("/"), bare):
        return url
    # Meta can't hyperlink anchor text, so keep the label and show the URL after it.
    return f"{label} {url}"


def strip_drive_links(text: str) -> str:
    """Drop every line that contains a Google Drive/Docs link (the asset folder)."""
    return "\n".join(line for line in text.split("\n") if not DRIVE_URL_RE.search(line))


def format_primary_text(raw: str) -> str:
    """Return primary text with one blank line between every line of copy."""
    if not raw:
        return ""
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = _INVISIBLE_RE.sub("", text)
    text = strip_drive_links(text)

    lines = []
    for line in text.split("\n"):
        line = _MD_LINK_RE.sub(_replace_link, line)
        line = _MD_AUTOLINK_RE.sub(r"\1", line)
        line = _MD_HEADING_RE.sub("", line)
        line = _MD_QUOTE_RE.sub("", line)
        line = _MD_BOLD_RE.sub(r"\2", line)
        line = _MD_STRIKE_RE.sub(r"\1", line)
        line = _MD_ESCAPE_RE.sub(r"\1", line)
        line = _SPACES_RE.sub(" ", line).strip()
        # Markdown horizontal rules / empty bullets carry no copy.
        if not line or re.fullmatch(r"[-*_]{3,}", line) or line in ("-", "*"):
            continue
        lines.append(line)

    return "\n\n".join(lines)

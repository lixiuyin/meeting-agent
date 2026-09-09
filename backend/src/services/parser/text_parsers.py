"""Plain text file parsers — TXT, CSV, HTML, JSON, RTF, etc."""

import logging
from pathlib import Path

from .types import PageContent, ParsedDocument

logger = logging.getLogger(__name__)


def _parse_text_file(file_path: Path) -> ParsedDocument:
    """Parse plain text files directly without OCR.

    Supports .txt, .md, .html, .json, .xml, .csv, etc.
    """
    suffix = file_path.suffix.lower()

    # Multi-encoding cascade: try common encodings for CJK and Western text
    _ENCODINGS = ["utf-8", "utf-8-sig", "gbk", "gb2312", "latin-1"]
    text: str | None = None
    for enc in _ENCODINGS:
        try:
            text = file_path.read_text(encoding=enc)
            break
        except (UnicodeDecodeError, ValueError):
            continue
    if text is None:
        # Last resort: binary mode with replacement characters
        text = file_path.read_bytes().decode("utf-8", errors="replace")

    # Handle specific formats
    if suffix == ".csv":
        text = _format_csv_as_text(file_path)
    elif suffix in {".html", ".htm"}:
        text = _strip_html_tags(text)
    elif suffix in {".json"}:
        text = _format_json_as_text(text)
    elif suffix == ".rtf":
        text = _strip_rtf_tags(text)

    page = PageContent(
        page_num=1,
        text=text,
        metadata={"file_type": suffix, "encoding": "utf-8"},
    )

    return ParsedDocument(
        file_type=suffix.lstrip("."),
        pages=[page],
        metadata={"source": str(file_path), "total_pages": 1},
        total_pages=1,
    )


def _format_csv_as_text(file_path: Path) -> str:
    """Convert CSV to readable text format."""
    import csv

    _ENCODINGS = ["utf-8", "utf-8-sig", "gbk", "gb2312", "latin-1"]
    rows: list[list[str]] | None = None
    for enc in _ENCODINGS:
        try:
            with open(file_path, encoding=enc, newline="") as f:
                rows = list(csv.reader(f))
                break
        except (UnicodeDecodeError, ValueError):
            continue
    if rows is None:
        with open(file_path, encoding="utf-8", errors="replace", newline="") as f:
            rows = list(csv.reader(f))

    # Format as table-like text
    lines = []
    for row in rows:
        lines.append(" | ".join(row))
    return "\n".join(lines)


def _strip_html_tags(html: str) -> str:
    """Extract visible text without treating HTML parsing as sanitizing HTML."""
    import re
    from html.parser import HTMLParser

    parts: list[str] = []

    class PlainTextParser(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.hidden_depth = 0

        def handle_starttag(self, tag, attrs):
            if tag in {"script", "style"}:
                self.hidden_depth += 1

        def handle_endtag(self, tag):
            if tag in {"script", "style"} and self.hidden_depth:
                self.hidden_depth -= 1

        def handle_startendtag(self, tag, attrs):
            # Script/style are non-void in HTML: a self-closing flag must not
            # expose their following contents as ordinary document text.
            self.handle_starttag(tag, attrs)

        def handle_data(self, data):
            if not self.hidden_depth:
                parts.append(data)

    parser = PlainTextParser()
    parser.feed(html)
    parser.close()
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def _format_json_as_text(json_str: str) -> str:
    """Format JSON as readable text."""
    import json

    try:
        data = json.loads(json_str)
        # Pretty print JSON
        return json.dumps(data, indent=2, ensure_ascii=False)
    except json.JSONDecodeError:
        return json_str


def _strip_rtf_tags(rtf: str) -> str:
    """Remove RTF control words and return plain text."""
    import re

    # Remove RTF header
    text = re.sub(r"\\rtf[^{]*", "", rtf)

    # Remove control words
    text = re.sub(r"\\[a-z]+\d*\s?", "", text)

    # Remove braces
    text = re.sub(r"[{}]", "", text)

    # Decode common escapes
    text = text.replace("\\'20", " ")
    text = text.replace("\\'0d", "\n")
    text = text.replace("\\'0a", "\n")
    text = text.replace("\\par", "\n")
    text = text.replace("\\tab", "\t")

    return text.strip()

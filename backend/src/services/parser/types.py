"""Data models for document parsing."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ImageAsset:
    """Structured image asset extracted from a document page."""

    asset_id: str
    page_num: int
    storage_path: str
    thumbnail_path: str | None = None
    bbox: tuple[float, float, float, float] | None = None
    caption: str | None = None
    ocr_text: str | None = None
    is_text_bearing: bool = False
    source_provider: str = ""


@dataclass(frozen=True)
class TableAsset:
    """Structured table asset extracted from a document page."""

    table_id: str
    page_num: int
    rows: tuple[tuple[str, ...], ...] = ()
    markdown: str = ""
    bbox: tuple[float, float, float, float] | None = None
    caption: str | None = None


@dataclass
class PageContent:
    """Content extracted from a single page/slide."""

    page_num: int
    text: str
    # Legacy field retained for compatibility with existing parser/provider payloads.
    images: list[dict] | None = None
    # Legacy field retained for compatibility with existing parser/provider payloads.
    tables: list[list[list[str]]] | None = None
    metadata: dict | None = None
    heading_path: tuple[str, ...] = ()
    image_assets: tuple[ImageAsset, ...] = ()
    table_assets: tuple[TableAsset, ...] = ()
    confidence: float | None = None


@dataclass
class ParsedDocument:
    """Structured document parsing result."""

    file_type: str
    pages: list[PageContent]
    metadata: dict
    total_pages: int
    parser_used: str = ""
    fallback_from: tuple[str, ...] = ()
    language: str | None = None

    def to_text(self) -> str:
        """Convert to plain text format for indexing.

        Page markers are excluded so they don't pollute vector chunks.
        Double newlines between pages preserve paragraph boundaries for
        the text splitter.
        """
        parts = []
        for page in self.pages:
            if page.text:
                parts.append(page.text)
        return "\n\n".join(parts)

    def to_indexable_text(self) -> str:
        """Like ``to_text()`` but also surfaces table markdown and image caption/OCR.

        Used by the text-chunking route so non-text documents do not silently
        drop tables and image text when bypassing the page-aware indexer.
        """
        parts: list[str] = []
        for page in self.pages:
            if page.text:
                parts.append(page.text)
            for table in page.table_assets or ():
                md = (table.markdown or "").strip()
                if md:
                    parts.append(md)
            for image in page.image_assets or ():
                caption = (image.caption or "").strip()
                ocr = (image.ocr_text or "").strip()
                if caption and ocr:
                    parts.append(f"[Caption] {caption}\n[OCR] {ocr}")
                elif caption:
                    parts.append(f"[Caption] {caption}")
                elif ocr:
                    parts.append(f"[OCR] {ocr}")
        return "\n\n".join(parts)

    def iter_pages(self):
        """Yield (page_num, text) tuples for per-page chunking."""
        for page in self.pages:
            if page.text:
                yield page.page_num, page.text


# Supported file extensions for document parsing (excluding video)
SUPPORTED_EXTS = frozenset(
    {
        # Documents
        ".pdf",
        ".ppt",
        ".pptx",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".csv",
        ".txt",
        ".md",
        ".markdown",
        ".html",
        ".htm",
        ".json",
        ".xml",
        ".rtf",
        # Images
        ".png",
        ".jpg",
        ".jpeg",
        ".bmp",
        ".tiff",
        ".tif",
        ".webp",
        ".gif",
    }
)

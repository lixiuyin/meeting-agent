"""Document parsing service — cloud-native content-aware routing.

Parsers are selected based on document characteristics:
  - Text-heavy PDFs → local fast path (free, instant)
  - Mixed layouts / images → Marker cloud API
  - Scanned / handwritten → MinerU cloud API
  - Single images → PaddleOCR layout-parsing API
  - All-cloud-failed → local PyMuPDF text extraction (last resort)

Configure via environment / config/main.yaml:
  OCR_PROVIDER: marker (default) | mineru | paddle  (hint, not fixed)
"""

# Silence PyMuPDF's native stderr chatter (e.g. ``MuPDF error: format error:
# No common ancestor in structure tree``). These are benign tagged-PDF
# structure complaints that don't affect rendering — they only flood the
# console because MuPDF writes them from C code, bypassing Python logging.
# Real failures still surface as exceptions through fitz's Python API.
try:
    import fitz  # type: ignore[import-not-found]

    fitz.TOOLS.mupdf_display_errors(False)  # type: ignore[attr-defined]
except Exception:
    # Best-effort; absent fitz or different PyMuPDF version is fine.
    pass  # no logger needed — fitz is optional

from ._errors import AllParsersFailedError, ParserProviderError
from ._http import close_parser_http_client
from .cascade import parse, parse_structured
from .types import SUPPORTED_EXTS, PageContent, ParsedDocument

__all__ = [
    "SUPPORTED_EXTS",
    "AllParsersFailedError",
    "PageContent",
    "ParsedDocument",
    "ParserProviderError",
    "close_parser_http_client",
    "parse",
    "parse_structured",
]

"""Meeting management API — shared router, constants, and helpers.

Sub-routers import the shared router and helper functions from this module.
Stdlib, FastAPI, database, and schema symbols should be imported directly
by each sub-router using three-level relative imports (per CLAUDE.md).
"""

from __future__ import annotations

import logging
import unicodedata
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException

from ....core.security import is_dev_user, verify_api_key
from ....models.schemas import FileType
from ....services.files._kinds import ALL_EXTENSIONS, kind_for_extension

if TYPE_CHECKING:
    from ....models.schemas import MeetingFileResponse, MeetingResponse

router = APIRouter(prefix="/meetings", tags=["meetings"], dependencies=[Depends(verify_api_key)])
logger = logging.getLogger(__name__)


def _ownership_filter(principal: dict) -> str | None:
    """Extract user_id for ownership filtering (None in dev mode)."""
    uid = principal.get("user_id", "default")
    return uid if not is_dev_user(uid) else None


# Transcript preview length
TRANSCRIPT_PREVIEW_LEN = 200

_KIND_TO_FILE_TYPE: dict[str, FileType] = {
    "video": FileType.VIDEO,
    "audio": FileType.AUDIO,
    "pdf": FileType.PDF,
    "pptx": FileType.PPT,
    "doc": FileType.DOC,
    "xls": FileType.XLS,
    "csv": FileType.CSV,
    "txt": FileType.TXT,
    "image": FileType.IMAGE,
}

# Allowed file extensions mapped to types.
FILE_TYPE_MAP: dict[str, FileType] = {
    ext: _KIND_TO_FILE_TYPE[spec.kind]
    for ext in ALL_EXTENSIONS
    if (spec := kind_for_extension(ext)) and spec.kind in _KIND_TO_FILE_TYPE
}

# Extensions whose containers begin with an ISO base-media `ftyp` box at offset 4.
# These files have variable box sizes and brand strings in the wild, so we only
# require the `ftyp` tag — not a specific size or brand — to avoid rejecting
# perfectly valid uploads (e.g. M4A with brand "isom"/"mp42", MOV with any size).
_FTYP_EXTS: frozenset[str] = frozenset({".mp4", ".m4v", ".mov", ".m4a", ".3gp"})

# Static magic-byte prefixes for file type validation (avoids adding python-magic).
# Audio/video formats with ftyp boxes are handled separately in `_validate_file_content`
# because their real-world headers are too variable for a fixed prefix match.
_MAGIC_BYTES: dict[str, list[bytes]] = {
    # Video
    ".mkv": [b"\x1a\x45\xdf\xa3"],  # EBML header
    ".avi": [b"RIFF"],
    ".webm": [b"\x1a\x45\xdf\xa3"],
    # Audio
    # MP3 is validated via sync-word predicate (see below); ID3v2 tag is the other valid prefix.
    ".mp3": [b"ID3"],
    ".wav": [b"RIFF"],  # WAV starts with RIFF (same as AVI, but we check extension)
    ".flac": [b"fLaC"],
    ".ogg": [b"OggS"],
    ".wma": [b"\x30\x26\xb2\x75"],  # ASF header
    ".opus": [b"OggS"],  # Opus uses Ogg container
    # Documents
    ".pdf": [b"%PDF"],
    ".ppt": [b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"],  # OLE2 (older PPT)
    ".pptx": [b"PK\x03\x04"],  # ZIP-based (OOXML)
    ".doc": [b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"],  # OLE2
    ".docx": [b"PK\x03\x04"],  # ZIP-based (OOXML)
    ".xls": [b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"],  # OLE2
    ".xlsx": [b"PK\x03\x04"],  # ZIP-based (OOXML)
    # Images
    ".png": [b"\x89PNG\r\n\x1a\n"],
    ".jpg": [b"\xff\xd8\xff"],
    ".jpeg": [b"\xff\xd8\xff"],
    ".bmp": [b"BM"],
    ".tiff": [b"II\x2a\x00", b"MM\x00\x2a"],  # LE and BE
    ".tif": [b"II\x2a\x00", b"MM\x00\x2a"],
    ".webp": [b"RIFF"],  # RIFF container; WEBP tag at offset 8 validated separately
    ".gif": [b"GIF87a", b"GIF89a"],
}


# Known valid ISO base-media ftyp major brands.  The top-level set is a
# catch-all for containers where the extension alone isn't enough to narrow
# the expected brand (e.g. generic .mp4 from unknown encoders).
_FTYP_VALID_BRANDS: frozenset[bytes] = frozenset(
    {
        b"mp42",
        b"isom",
        b"iso2",
        b"iso3",
        b"iso4",
        b"iso5",
        b"iso6",
        b"iso7",
        b"iso8",
        b"iso9",
        b"avc1",
        b"av01",
        b"M4A ",
        b"M4B ",
        b"M4P ",
        b"M4V ",
        b"qt  ",
        b"3gp4",
        b"3gp5",
        b"3gp6",
        b"3gp7",
        b"3g2a",
        b"3ge6",
        b"3ge7",
        b"3gg6",
        b"MSNV",
        b"mp41",
    }
)

# Extension-specific brand restrictions for better precision (M-1).
# Extensions not listed here fall through to _FTYP_VALID_BRANDS above.
# Only disallow brands that are clearly wrong for a given container (e.g. qt for
# .mp4 or mp42 for .mov); keep common cross-container brands like isom/mp42 for
# audio containers where real-world encoders (iTunes/ffmpeg) use them.
_FTYP_BRANDS_BY_EXT: dict[str, frozenset[bytes]] = {
    ".mp4": frozenset({b"mp42", b"isom", b"avc1", b"mp41", b"iso2", b"iso5", b"iso6", b"av01"}),
    ".mov": frozenset({b"qt  "}),
    ".m4a": frozenset({b"M4A ", b"M4B ", b"M4P ", b"isom", b"mp42", b"mp41"}),
    ".m4v": frozenset({b"M4V ", b"mp42", b"isom", b"avc1", b"mp41"}),
    ".3gp": frozenset({b"3gp4", b"3gp5", b"3gp6", b"3gp7", b"3g2a", b"3ge6", b"3ge7", b"3gg6"}),
}


def _is_ftyp_header(file_content: bytes, ext: str | None = None) -> bool:
    """Check for an ISO base-media `ftyp` box at offset 4 with a known brand.

    When *ext* is provided, brand validation is narrowed to the known-good
    brands for that container format (M-1).  Otherwise the global brand set
    is used as a catch-all.
    """
    if len(file_content) < 12 or file_content[4:8] != b"ftyp":
        return False
    major_brand = file_content[8:12]
    if ext and ext.lower() in _FTYP_BRANDS_BY_EXT:
        return major_brand in _FTYP_BRANDS_BY_EXT[ext.lower()]
    return major_brand in _FTYP_VALID_BRANDS


def _is_mp3_sync(file_content: bytes) -> bool:
    """Check for a valid MPEG audio frame sync word.

    The MPEG sync word is 11 bits of 1s: byte0 == 0xff and (byte1 & 0xe0) == 0xe0.
    This covers all MPEG-1/2/2.5 Layer I/II/III headers, including the common
    MP3 variants \\xff\\xfa, \\xff\\xfb, \\xff\\xf2, \\xff\\xf3, \\xff\\xe2, \\xff\\xe3.
    """
    return len(file_content) >= 2 and file_content[0] == 0xFF and (file_content[1] & 0xE0) == 0xE0


# M-18: Maximum filename byte length.  Longer names are truncated at the
# basename level, preserving the extension.
_MAX_FILENAME_BYTES = 200


def _sanitize_filename(filename: str) -> str:
    """Strip path components and dangerous characters from a user-supplied filename.

    M-18: Also enforces a 200-byte length cap to prevent filesystem issues.
    Callers are responsible for symlink checks on the final written path.
    """
    # Take only the basename (prevents ../../ traversal)
    name = Path(filename).name
    # Normalize Unicode to NFC to prevent visually-similar filename attacks
    name = unicodedata.normalize("NFC", name)
    # Remove any remaining path separators and null bytes
    for ch in ("\x00", "/", "\\"):
        name = name.replace(ch, "_")
    # Strip unicode directional overrides (bidirectional text attacks)
    bidi_overrides = (
        "\u202a",
        "\u202b",
        "\u202c",
        "\u202d",
        "\u202e",
        "\u2066",
        "\u2067",
        "\u2068",
        "\u2069",
    )
    for ch in bidi_overrides:
        name = name.replace(ch, "")
    # Block Windows reserved names (base and stem)
    reserved = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
    base = name.split(".")[0].upper()
    stem = Path(name).stem.upper()
    if base in reserved or stem in reserved:
        name = f"_{name}"
    # M-18: Truncate to 200 bytes, preserving extension
    if len(name.encode("utf-8")) > _MAX_FILENAME_BYTES:
        ext = Path(name).suffix
        base_name = Path(name).stem
        # Shrink base name to fit extension within 200 bytes
        while len((base_name + ext).encode("utf-8")) > _MAX_FILENAME_BYTES and base_name:
            base_name = base_name[:-1]
        name = f"{base_name}{ext}" if base_name else f"upload{ext}"
    return name or "upload"


def _validate_file_content(file_content: bytes, expected_ext: str) -> None:
    """Validate that file content matches expected type by checking magic bytes.

    Raises HTTPException if mismatch detected. Audio/video containers with
    variable headers (MP4/M4A/MOV/M4V/3GP ftyp box, MP3 sync word) are matched
    with permissive predicates rather than fixed byte prefixes, so real-world
    files from different encoders are not wrongly rejected.
    """
    if not file_content:
        raise ValueError("empty file")

    ext = expected_ext.lower()

    # ISO base-media containers: accept any ftyp box (any size, any brand).
    if ext in _FTYP_EXTS:
        if _is_ftyp_header(file_content, ext=ext):
            return
        raise HTTPException(400, f"File content does not match format for {expected_ext}")

    # MP3: accept either an ID3v2 tag or a raw MPEG frame sync word.
    if ext == ".mp3":
        if _is_mp3_sync(file_content) or file_content.startswith(b"ID3"):
            return
        raise HTTPException(400, f"File content does not match format for {expected_ext}")

    # WEBP: RIFF container with WEBP tag at offset 8 (bytes 4-7 are file size, variable).
    if ext == ".webp":
        if (
            len(file_content) >= 12
            and file_content[:4] == b"RIFF"
            and file_content[8:12] == b"WEBP"
        ):
            return
        raise HTTPException(400, f"File content does not match format for {expected_ext}")

    magic = _MAGIC_BYTES.get(ext)
    if not magic:
        return  # Unknown type, skip validation

    for expected in magic:
        if file_content.startswith(expected):
            return

    raise HTTPException(400, f"File content does not match format for {expected_ext}")


def _build_meeting_response(m: dict, *, file_types: list[str] | None = None) -> MeetingResponse:
    """Convert a database row dict into a MeetingResponse.

    *file_types* is the list of distinct file_types present in the meeting's
    files (used to render mixed-modality meetings).  When omitted, falls back
    to ``[m['file_type']]`` so callers that don't have the aggregate yet still
    produce a non-empty list for single-file meetings.
    """
    from ....models.schemas import MeetingResponse, MeetingStatus

    preview = None
    transcript = m.get("transcript")
    if transcript and len(transcript) > TRANSCRIPT_PREVIEW_LEN:
        preview = transcript[:TRANSCRIPT_PREVIEW_LEN] + "..."
    elif transcript:
        preview = transcript

    if file_types is None:
        file_types = [m["file_type"]] if m.get("file_type") else []
    typed_file_types = [FileType(t) for t in file_types if t]

    return MeetingResponse(
        id=m["id"],
        title=m["title"],
        description=m["description"],
        file_type=FileType(m["file_type"]) if m.get("file_type") else None,
        file_name=m.get("file_name"),
        file_types=typed_file_types,
        status=MeetingStatus(m["status"]),
        meeting_date=m["meeting_date"],
        created_at=m["created_at"],
        transcript_preview=preview,
        error_message=m.get("error_message"),
        file_url=None,
    )


def _build_meeting_file_response(f: dict) -> MeetingFileResponse:
    """Convert a meeting file row into MeetingFileResponse"""
    from ....models.schemas import MeetingFileResponse, MeetingStatus

    preview = None
    transcript = f.get("transcript")
    if transcript and len(transcript) > TRANSCRIPT_PREVIEW_LEN:
        preview = transcript[:TRANSCRIPT_PREVIEW_LEN] + "..."
    elif transcript:
        preview = transcript
    return MeetingFileResponse(
        id=f["id"],
        file_type=FileType(f["file_type"]),
        file_name=f["file_name"],
        status=MeetingStatus(f["status"]),
        created_at=f["created_at"],
        transcript_preview=preview,
        error_message=f.get("error_message"),
        summary=f.get("summary"),
        summary_status=f.get("summary_status"),
    )

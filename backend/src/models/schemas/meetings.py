"""Meeting-related Pydantic models."""

from pydantic import BaseModel, Field

from ._common import ExportFormat, FileType, MeetingStatus, UTCDatetime


class MeetingResponse(BaseModel):
    id: int
    title: str
    description: str | None = None
    file_type: FileType | None = None
    file_name: str | None = None
    file_types: list[FileType] = Field(
        default_factory=list,
        description="Distinct file types across all files attached to the meeting; "
        "use len() > 1 to detect mixed-modality meetings.",
    )
    status: MeetingStatus
    meeting_date: UTCDatetime | None = None
    created_at: UTCDatetime
    transcript_preview: str | None = Field(None, description="First 200 characters of transcript")
    error_message: str | None = Field(None, description="Failure reason when status is 'failed'")
    file_url: str | None = Field(None, description="URL to download/stream the original file")


class MeetingListResponse(BaseModel):
    total: int
    meetings: list[MeetingResponse]


class MeetingFileResponse(BaseModel):
    """A file attached to a meeting"""

    id: int
    file_type: FileType
    file_name: str
    status: MeetingStatus
    created_at: UTCDatetime
    transcript_preview: str | None = Field(None, description="First 200 characters of transcript")
    error_message: str | None = Field(None, description="Failure reason when status is 'failed'")
    summary: str | None = Field(None, description="Per-file summary when available")
    summary_status: str | None = Field(
        None, description="File summary status: pending|generating|ready|failed"
    )


class MeetingDetailResponse(BaseModel):
    """Meeting with full file list"""

    id: int
    title: str
    description: str | None = None
    status: MeetingStatus
    meeting_date: UTCDatetime | None = None
    created_at: UTCDatetime
    updated_at: UTCDatetime
    files: list[MeetingFileResponse]
    summary_status: str | None = Field(
        None, description="Meeting-level summary status: pending|ready|failed"
    )
    summary: str | None = Field(None, description="Meeting-level summary text when ready")


class CreateMeetingRequest(BaseModel):
    """Create a new empty meeting"""

    title: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    meeting_date: UTCDatetime | None = Field(None, description="When the meeting took place")


class CreateMeetingResponse(BaseModel):
    meeting_id: int
    message: str


class UploadResponse(BaseModel):
    meeting_id: int
    file_id: int | None = Field(None, description="ID of the created file record")
    message: str
    is_existing: bool = Field(False, description="True if this file was already uploaded")


class PerFileSummary(BaseModel):
    """Persisted or generated summary of one file within a meeting."""

    file_id: int
    file_name: str
    file_type: FileType
    summary: str
    key_points: list[str] = Field(default_factory=list)


class SummaryResponse(BaseModel):
    """Meeting summary response"""

    meeting_id: int
    status: str = Field(
        "ready",
        description="Summary lifecycle state: pending | generating | ready | failed",
    )
    summary: str | None = Field(None, description="Generated summary of the meeting")
    tokens_used: int | None = Field(None, description="Number of tokens used for generation")
    per_file_summaries: list[PerFileSummary] = Field(default_factory=list)


class UpdateMeetingRequest(BaseModel):
    """Update meeting metadata request"""

    title: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    meeting_date: UTCDatetime | None = None


class TranscriptFileItem(BaseModel):
    file_id: int
    file_name: str
    file_type: FileType
    content: str = Field(description="Transcript/content for this file")


class TranscriptResponse(BaseModel):
    """Full transcript response"""

    meeting_id: int
    transcript: str | None = Field(None, description="Full meeting transcript text")
    format: str = Field(default="plain", description="Format: plain or markdown")
    files: list[TranscriptFileItem] = Field(
        default_factory=list,
        description="Per-file transcript payload",
    )


class MeetingSearchResult(BaseModel):
    """Search result item for meeting content search"""

    meeting_id: int
    title: str
    file_type: FileType
    snippet: str = Field(description="Matching text snippet with context")
    score: float = Field(description="Search relevance score")


class MeetingSearchResponse(BaseModel):
    """Meeting content search response"""

    query: str
    total: int
    results: list[MeetingSearchResult]


class TranscriptSegment(BaseModel):
    """A single transcript segment with timestamp"""

    start: float = Field(description="Start time in seconds")
    end: float = Field(description="End time in seconds")
    text: str = Field(description="Transcript text")
    speaker: str | None = Field(None, description="Speaker label (code or name)")


class SpeakerInfo(BaseModel):
    """Speaker code with optional real name and sample utterance."""

    speaker_code: str = Field(description="Raw speaker code from ASR (e.g. 'A', 'B')")
    speaker_name: str | None = Field(None, description="User-assigned real name")
    utterance_count: int = Field(description="Number of utterances by this speaker")
    sample: TranscriptSegment | None = Field(None, description="First non-empty utterance")


class SpeakersResponse(BaseModel):
    """List of speakers for a meeting file."""

    file_id: int
    speakers: list[SpeakerInfo]


class SpeakerNameMapping(BaseModel):
    """A single speaker code → name mapping."""

    speaker_code: str = Field(..., min_length=1)
    speaker_name: str = Field(..., min_length=1)


class UpdateSpeakersRequest(BaseModel):
    """Assign real names to speaker codes."""

    mappings: list[SpeakerNameMapping]


class UpdateSpeakersResponse(BaseModel):
    """Result of updating speaker names."""

    file_id: int
    mappings: list[SpeakerInfo]
    message: str


class TranscriptWithTimestampsResponse(BaseModel):
    """Meeting transcript with timestamp segments"""

    meeting_id: int
    segments: list[TranscriptSegment]
    total_duration: float | None = Field(None, description="Total duration in seconds")
    speaker_count: int | None = Field(None, description="Estimated speaker count (if available)")


class ReprocessResponse(BaseModel):
    """Response for POST /meetings/{id}/reprocess"""

    message: str
    meeting_id: int


# ---------------------------------------------------------------------------
# Export payload models
# ---------------------------------------------------------------------------


class ExportImageItem(BaseModel):
    """Image asset in an export payload."""

    url: str = Field(description="Absolute URL to the image asset")
    storage_path: str = Field(description="Relative storage path")
    caption: str | None = Field(None, description="VLM-generated caption")
    ocr_text: str | None = Field(None, description="OCR extracted text")


class ExportPageItem(BaseModel):
    """A single page in a document export."""

    page_num: int = Field(description="Page number (1-based)")
    heading: str | None = Field(None, description="Page heading")
    text: str = Field(description="Page text content")
    images: list[ExportImageItem] = Field(default_factory=list)


class ExportFilePages(BaseModel):
    """Export payload for a paginated document (PDF/PPTX)."""

    file_id: int
    file_name: str
    file_type: str
    kind: str = Field("pages", description="Discriminant")
    summary: str | None = None
    pages: list[ExportPageItem] = Field(default_factory=list)


class ExportFileCaptions(BaseModel):
    """Export payload for an image with captions/OCR."""

    file_id: int
    file_name: str
    file_type: str
    kind: str = Field("captions", description="Discriminant")
    image_url: str | None = Field(None, description="URL to the original image")
    images: list[ExportImageItem] = Field(default_factory=list)


class ExportFileSegments(BaseModel):
    """Export payload for audio/video with segments."""

    file_id: int
    file_name: str
    file_type: str
    kind: str = Field("segments", description="Discriminant")
    summary: str | None = None
    segments: list[TranscriptSegment] = Field(default_factory=list)
    total_duration: float | None = None


class ExportFileText(BaseModel):
    """Export payload for plain text files."""

    file_id: int
    file_name: str
    file_type: str
    kind: str = Field("text", description="Discriminant")
    summary: str | None = None
    text: str = ""
    word_count: int = 0


ExportFilePayload = ExportFilePages | ExportFileCaptions | ExportFileSegments | ExportFileText


class ExportMeetingPayload(BaseModel):
    """Structured JSON export payload."""

    meeting_id: int
    title: str
    description: str | None = None
    meeting_date: str | None = None
    created_at: str
    transcript: str = Field("", description="Flat transcript for legacy consumers")
    files: list[ExportFilePayload] = Field(default_factory=list)


class ExportResponse(BaseModel):
    """Meeting export response"""

    meeting_id: int
    format: ExportFormat
    content: str | None = Field(None, description="Exported content (MD/TXT)")
    data: ExportMeetingPayload | None = Field(None, description="Structured payload (JSON)")
    filename: str = Field(description="Suggested filename")
    content_type: str = Field(description="MIME content type")


# ---------------------------------------------------------------------------
# Per-file timeline (discriminated union by kind)
# ---------------------------------------------------------------------------


class TimelineSegmentItem(BaseModel):
    """A single segment in an audio/video timeline."""

    start: float = Field(description="Start time in seconds")
    end: float = Field(description="End time in seconds")
    text: str = Field(description="Segment text")
    speaker: str | None = Field(None, description="Speaker label")


class TimelinePageItem(BaseModel):
    """A single page in a document timeline."""

    class TimelinePageImageAsset(BaseModel):
        storage_path: str = Field(description="Relative storage path under uploads")
        thumbnail_path: str | None = Field(None, description="Relative thumbnail path")
        caption: str | None = Field(None, description="Image caption text")
        ocr_text: str | None = Field(None, description="OCR text for the image")

    page_num: int = Field(description="Page number (1-based)")
    text: str = Field(description="Page text content")
    heading: str | None = Field(None, description="Page heading or title, if detected")
    image_assets: list[TimelinePageImageAsset] = Field(
        default_factory=list, description="Extracted image assets on the page"
    )


class SegmentsTimeline(BaseModel):
    """Timeline response for audio/video files."""

    kind: str = Field("segments", description="Discriminant: always 'segments'")
    file_id: int
    file_name: str
    segments: list[TimelineSegmentItem]
    total_duration: float | None = Field(None, description="Total duration in seconds")
    speaker_count: int | None = Field(None, description="Number of distinct speakers")


class PagesTimeline(BaseModel):
    """Timeline response for paginated documents (PDF, PPTX)."""

    kind: str = Field("pages", description="Discriminant: always 'pages'")
    file_id: int
    file_name: str
    pages: list[TimelinePageItem]
    page_count: int = Field(description="Total number of pages")


class TextTimeline(BaseModel):
    """Timeline response for plain text files."""

    kind: str = Field("text", description="Discriminant: always 'text'")
    file_id: int
    file_name: str
    text: str = Field(description="Full text content")
    word_count: int = Field(description="Number of words")


class TimelineCaptionItem(BaseModel):
    """A caption/ocr record for image-like files."""

    caption: str | None = Field(None, description="Vision caption text")
    ocr_text: str | None = Field(None, description="OCR extracted text")


class CaptionsTimeline(BaseModel):
    """Timeline response for image/captioned files."""

    kind: str = Field("captions", description="Discriminant: always 'captions'")
    file_id: int
    file_name: str
    captions: list[TimelineCaptionItem] = Field(default_factory=list)


# Union type for the endpoint response_model annotation
FileTimelineResponse = SegmentsTimeline | PagesTimeline | TextTimeline | CaptionsTimeline

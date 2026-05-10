export interface TimestampSegment {
  start: number;
  end: number;
  text: string;
  speaker?: string | null;
}

export interface TimestampPlayback {
  meetingId: number;
  fileId: number;
  fileName: string;
  fileType: string;
}

const UNNAMED_SPEAKER_CODE_PATTERN = /^[A-Z]$|^SPEAKER[_\s-]?\d+$/i;

export function isUnnamedSpeakerCode(speaker?: string | null): boolean {
  if (!speaker) return false;
  const normalized = speaker.trim();
  return UNNAMED_SPEAKER_CODE_PATTERN.test(normalized);
}

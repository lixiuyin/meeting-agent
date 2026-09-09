import { useCallback, useEffect, useRef, useState } from "react";
import { message } from "antd";
import {
  getFileSpeakers,
  getSpeakerAudioUrl,
  updateSpeakerNames,
  formatApiErrorMessage,
  type MeetingInfo,
  type SpeakersResponse,
} from "../../api/client";
import type { TimestampPlayback } from "./types";
import { reportNonCriticalError } from "../../utils/monitoring";

interface Options {
  fetchMeetings: () => void;
  detailMeeting: MeetingInfo | null;
  handleOpenDetail: (meeting: MeetingInfo) => Promise<void>;
  timestampsOpen: boolean;
  timestampsPlayback: TimestampPlayback | null;
  refreshTimestamps: (meetingId: number, fileId: number) => Promise<void>;
  onSummaryRefresh?: (meetingId: number) => void;
}

export function useMeetingSpeakers({
  fetchMeetings,
  detailMeeting,
  handleOpenDetail,
  timestampsOpen,
  timestampsPlayback,
  refreshTimestamps,
  onSummaryRefresh,
}: Options) {
  const [speakerModalOpen, setSpeakerModalOpen] = useState(false);
  const [speakerMeetingId, setSpeakerMeetingId] = useState<number | null>(null);
  const [speakerData, setSpeakerData] = useState<SpeakersResponse | null>(null);
  const [speakerLoading, setSpeakerLoading] = useState(false);
  const [speakerNames, setSpeakerNames] = useState<Record<string, string>>({});
  const [speakerPlaying, setSpeakerPlaying] = useState<string | null>(null);
  const [speakerSaving, setSpeakerSaving] = useState(false);
  const activeAudioRef = useRef<HTMLAudioElement | null>(null);

  useEffect(() => {
    return () => {
      activeAudioRef.current?.pause();
      activeAudioRef.current?.removeAttribute("src");
      activeAudioRef.current = null;
    };
  }, []);

  const handleOpenSpeakers = useCallback(async (meetingId: number, fileId: number) => {
    setSpeakerModalOpen(true);
    setSpeakerMeetingId(meetingId);
    setSpeakerLoading(true);
    setSpeakerData(null);
    try {
      const res = await getFileSpeakers(meetingId, fileId);
      setSpeakerData(res.data);
      const names: Record<string, string> = {};
      for (const s of res.data.speakers) {
        if (s.speaker_name) names[s.speaker_code] = s.speaker_name;
      }
      setSpeakerNames(names);
    } catch (err) {
      message.error(formatApiErrorMessage(err, "Failed to load speakers"));
    } finally {
      setSpeakerLoading(false);
    }
  }, []);

  const handlePlaySpeaker = useCallback(
    async (meetingId: number, fileId: number, speakerCode: string) => {
      if (activeAudioRef.current) {
        activeAudioRef.current.pause();
        activeAudioRef.current.removeAttribute("src");
      }

      setSpeakerPlaying(speakerCode);
      let url: string;
      try {
        url = await getSpeakerAudioUrl(meetingId, fileId, speakerCode);
      } catch (error) {
        reportNonCriticalError("resolve speaker audio URL", error);
        message.error("Failed to prepare audio sample");
        setSpeakerPlaying(null);
        return;
      }
      const audio = new Audio(url);
      activeAudioRef.current = audio;

      audio.onended = () => {
        setSpeakerPlaying(null);
        if (activeAudioRef.current === audio) activeAudioRef.current = null;
      };
      audio.onerror = () => {
        message.error("Failed to play audio sample");
        setSpeakerPlaying(null);
        if (activeAudioRef.current === audio) activeAudioRef.current = null;
      };
      audio.play().catch(() => {
        reportNonCriticalError("play speaker audio", new Error("Audio playback rejected"));
        setSpeakerPlaying(null);
        if (activeAudioRef.current === audio) activeAudioRef.current = null;
      });
    },
    [],
  );

  const stopAudio = useCallback(() => {
    if (activeAudioRef.current) {
      activeAudioRef.current.pause();
      activeAudioRef.current.removeAttribute("src");
      activeAudioRef.current = null;
    }
    setSpeakerPlaying(null);
  }, []);

  const handleSaveSpeakers = useCallback(async () => {
    if (speakerMeetingId == null || !speakerData) {
      message.error("Speaker context is missing. Please reopen Identify Speakers.");
      return;
    }
    const meetingId = speakerMeetingId;
    const fileId = speakerData.file_id;
    setSpeakerSaving(true);
    try {
      const mappings = Object.entries(speakerNames)
        .filter(([, name]) => name.trim())
        .map(([code, name]) => ({ speaker_code: code, speaker_name: name.trim() }));
      if (mappings.length === 0) {
        message.warning("Please enter at least one speaker name");
        setSpeakerSaving(false);
        return;
      }
      await updateSpeakerNames(meetingId, fileId, mappings);
      message.success("Speaker names saved");
      setSpeakerModalOpen(false);
      if (detailMeeting?.id === meetingId) {
        void handleOpenDetail({ ...detailMeeting } as MeetingInfo);
      }
      fetchMeetings();
      if (timestampsOpen && detailMeeting?.id === meetingId) {
        const playbackFileId = timestampsPlayback?.fileId ?? fileId;
        void refreshTimestamps(meetingId, playbackFileId);
      }
      onSummaryRefresh?.(meetingId);
    } catch (err) {
      message.error(formatApiErrorMessage(err, "Failed to save speaker names"));
    } finally {
      setSpeakerSaving(false);
    }
  }, [
    speakerMeetingId,
    speakerData,
    speakerNames,
    detailMeeting,
    handleOpenDetail,
    fetchMeetings,
    timestampsOpen,
    timestampsPlayback,
    refreshTimestamps,
    onSummaryRefresh,
  ]);

  return {
    speakerModalOpen,
    setSpeakerModalOpen,
    speakerMeetingId,
    speakerData,
    speakerLoading,
    speakerNames,
    setSpeakerNames,
    speakerPlaying,
    setSpeakerPlaying,
    speakerSaving,
    handleOpenSpeakers,
    handlePlaySpeaker,
    stopAudio,
    handleSaveSpeakers,
  };
}
